"""全学習ユーザーの推薦モデルValidation / 集約履歴を順次読み、個別結果をParquet保存。"""

import argparse
import json
import os
import time
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

import numpy as np

from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.research.store import atomic_json
from src.paths import PROJECT_ROOT


def run(
    config_path,
    *,
    model_cache=None,
    export_path=None,
    model_version=None,
    phase="validation",
    gpu_popularity=False,
    gpu_itemcf=False,
    als_backend=None,
    ease_memory_budget_mb=None,
):
    import duckdb

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.provenance import source_hash
    from src.infrastructure.research.search import StudyConfig, load_study_config, study_lock

    config = config_path if isinstance(config_path, StudyConfig) else load_study_config(config_path)
    if phase not in {"validation", "test"}:
        raise ValueError("phase must be validation or test")
    if phase == "test":
        if (
            config.model not in {"popularity", "itemcf", "als", "bpr", "ease", "content", "bm25"}
            or config.test_dataset is None
        ):
            raise ValueError("Full Test requires a supported full model and a Test bundle")
        from src.infrastructure.research.results import read_result, verify_artifacts

        validation = read_result(config)
        if validation.get("status") != "succeeded" or validation.get("phase") != "validation":
            raise ValueError("Test requires a succeeded matching Validation result")
        verify_artifacts(config.output, validation["artifacts"])
        validation_evidence = {
            name: {
                "path": str(config.output / f"{name}.json"),
                "sha256": file_sha256(config.output / f"{name}.json"),
            }
            for name in ("config", "result")
        }
        config = replace(config, output=config.output / "test")
        if config.output.exists():
            raise FileExistsError(
                "Final Test output exists; saved or partial evidence is preserved"
            )
    if gpu_popularity and config.model != "popularity":
        raise ValueError("gpu_popularity requires a Popularity model")
    if gpu_itemcf and config.model != "itemcf":
        raise ValueError("gpu_itemcf requires an ItemCF model")
    if als_backend is not None and (config.model != "als" or als_backend not in {"cpu", "cuda"}):
        raise ValueError("als_backend requires ALS and cpu or cuda")
    if ease_memory_budget_mb is not None:
        from src.infrastructure.recommenders.factors import number

        number(ease_memory_budget_mb, "ease_memory_budget_mb", positive=True)
        if config.model != "ease" or not config.parameters.get("use_gpu"):
            raise ValueError("ease_memory_budget_mb requires GPU EASE")
    if (export_path is None) != (model_version is None):
        raise ValueError("export_path and model_version must be specified together")
    if model_version is not None:
        from src.domain.recommendation.contracts import validate_id

        validate_id(model_version)
        if Path(export_path).resolve() == config.output.resolve():
            raise ValueError("model export directory must differ from experiment output")
    if export_path is not None and Path(export_path).exists():
        raise FileExistsError("配信用モデルは上書きしません")
    allowed = {
        "popularity": {"basis"},
        "itemcf": {"neighbors", "similarity"},
        "content": {"use_idf", "aggregation", "use_gpu"},
        "bm25": {"neighbors", "k1", "b", "use_gpu"},
        "ease": {"regularization", "use_gpu", "block_size", "memory_budget_mb"},
        "bpr": {
            "factors",
            "iterations",
            "regularization",
            "learning_rate",
            "memory_budget_mb",
            "use_gpu",
        },
        "als": {
            "factors",
            "iterations",
            "regularization",
            "alpha",
            "confidence_transform",
            "use_gpu",
            "cg_steps",
            "memory_budget_mb",
        },
    }
    if (
        config.model not in allowed
        or config.protocol != "listening-v2"
        or config.parameters.keys() - allowed.get(config.model, set())
        or config.grid
    ):
        raise ValueError(
            "full evaluation requires listening-v2 popularity/itemcf/als/bpr/content/bm25/ease with fixed parameters"
        )
    if config.model in {"content", "ease"} and export_path is not None:
        raise ValueError(f"{config.model} serving export is not implemented; omit --export-model")
    if config.model in {"als", "bpr"} and len(config.seeds) != 1:
        raise ValueError("full factor model requires exactly one seed per run")
    from src.infrastructure.recommenders.popularity import validate_basis

    basis = validate_basis(config.parameters.get("basis", "play_count"))
    ordering = "sum(play_count)" if basis == "play_count" else "count(*)"
    output = config.output
    output.mkdir(parents=True, exist_ok=True)
    with study_lock(config):
        if (output / "result.json").exists():
            raise FileExistsError("結果は上書きしません / 新しい出力先を指定してください")
        with (output / "run.log").open("a") as log:

            def progress(message):
                print(message, flush=True)
                log.write(message + "\n")
                log.flush()

            progress(f"Validating full {phase} bundle")
            dataset = config.test_dataset if phase == "test" else config.validation_dataset
            manifest, paths, counts = validate_bundle(dataset)
            if manifest["phase"] != phase:
                raise ValueError("Input phase differs from requested phase")
            if phase == "test":
                from src.infrastructure.research.full_test import validate_training

                validate_training(config, manifest, paths, counts)
            use_gpu = gpu_popularity or gpu_itemcf or config.parameters.get("use_gpu", False)
            training_parameters = dict(config.parameters)
            if als_backend is not None:
                use_gpu = als_backend == "cuda"
                training_parameters["use_gpu"] = use_gpu
                training_parameters.setdefault("cg_steps", 20)
            if ease_memory_budget_mb is not None:
                training_parameters["memory_budget_mb"] = ease_memory_budget_mb
            if gpu_popularity or gpu_itemcf:
                from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

                progress("GPU preflight: " + require_idle_gpu())
            features = load_features(
                config.features,
                dataset_id=manifest["dataset_id"],
                snapshot_id=manifest["source"]["snapshot_id"],
            )
            started = time.perf_counter()
            with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as c:
                ranked = c.execute(
                    f"SELECT item_id,sum(play_count)::BIGINT,count(*) FROM read_parquet(?) GROUP BY item_id ORDER BY {ordering} DESC,item_id",
                    [str(paths["learning"])],
                ).fetchall()
            itemcf = None
            if config.model == "itemcf":
                from src.infrastructure.recommenders.full_itemcf import FullItemCF

                itemcf = FullItemCF(
                    paths["learning"],
                    progress=progress,
                    cache_dir=model_cache,
                    use_gpu=gpu_itemcf,
                    **config.parameters,
                )
            factor_model = None
            if config.model == "bm25":
                from src.infrastructure.recommenders.full_bm25 import FullBM25

                factor_model = FullBM25(paths["learning"], progress=progress, **config.parameters)
            if config.model == "ease":
                from src.infrastructure.recommenders.full_ease import FullEASE

                factor_model = FullEASE(paths["learning"], progress=progress, **training_parameters)
            if config.model == "content":
                from src.infrastructure.recommenders.full_content import FullContent

                factor_model = FullContent(
                    paths["learning"], features=features, progress=progress, **config.parameters
                )
            if config.model in {"als", "bpr"}:
                from src.infrastructure.recommenders.full_als import FullALS
                from src.infrastructure.recommenders.full_bpr import FullBPR

                factor_type = FullALS if config.model == "als" else FullBPR
                factor_model = factor_type(
                    paths["learning"],
                    seed=config.seeds[0],
                    progress=progress,
                    **training_parameters,
                )
            training_seconds = (
                itemcf.training_seconds if itemcf is not None else time.perf_counter() - started
            )
            if factor_model is not None:
                training_seconds = factor_model.training_seconds
            if gpu_popularity:
                from src.infrastructure.recommenders.full_popularity import FullPopularity
                from src.infrastructure.recommenders.gpu_runtime import gpu_metadata

                factor_model = FullPopularity(paths["learning"], basis=basis)
                training_seconds = time.perf_counter() - started
                factor_model.training_seconds = training_seconds
                factor_model.execution = {
                    **gpu_metadata(),
                    "backend": "cupy CUDA",
                    "training_device": "cpu",
                    "solver": "exact popularity aggregation; stable GPU ranking",
                    "dtype": "float64",
                    "random_computation": False,
                    "bitwise_reproducible": False,
                }
            popularity = [row[0] for row in ranked]
            rank = {item: j for j, item in enumerate(popularity)}
            item_users = {item: users for item, _, users in ranked}
            atomic_json(
                output / "config.json",
                {
                    "format": "music-recsys-full-config-v1",
                    "settings": json.loads(json.dumps(asdict(config), default=str)),
                    "manifest": manifest,
                    "features_sha256": file_sha256(config.features),
                    "source_sha256": source_hash(),
                    "dependency_lock_sha256": file_sha256(PROJECT_ROOT / "uv.lock"),
                    "execution": (
                        {
                            "solver": "exact normal equations float64",
                            **getattr(
                                factor_model if factor_model is not None else itemcf,
                                "execution",
                                {},
                            ),
                            "evaluation_device": "cuda" if use_gpu else "cpu",
                            "evaluation_batch_size": 128 if use_gpu else 1,
                            **(
                                {"cupy": version("cupy-cuda12x"), "metric_dtype": "float64"}
                                if use_gpu
                                else {}
                            ),
                            "seed": config.seeds[0],
                            **(
                                {"training_parameters": training_parameters}
                                if als_backend or ease_memory_budget_mb is not None
                                else {}
                            ),
                            "numpy": np.__version__,
                            "threads": {
                                key: os.environ.get(key)
                                for key in (
                                    "OPENBLAS_NUM_THREADS",
                                    "OMP_NUM_THREADS",
                                    "MKL_NUM_THREADS",
                                )
                            },
                        }
                        if factor_model is not None or gpu_itemcf
                        else f"deterministic {config.model} once; no seed-dependent computation"
                    ),
                    "population": counts,
                    "phase": phase,
                    "input_dataset": str(dataset),
                    **(
                        {
                            "validation_evidence": validation_evidence,
                            "training_periods": ["train", "validation"],
                        }
                        if phase == "test"
                        else {}
                    ),
                    "training_cache_hit": itemcf.cache_hit if itemcf is not None else False,
                },
            )
            if use_gpu:
                from src.infrastructure.evaluation.gpu_evaluation import score_gpu as scorer
            else:
                from src.infrastructure.evaluation.full_scoring import score_cpu as scorer
            rows, beyond, efficiency = scorer(
                config,
                paths,
                counts,
                features,
                item_users,
                popularity,
                rank,
                factor_model,
                itemcf,
                progress,
            )
            efficiency["training_seconds"] = training_seconds
            artifacts = {}
            for name in ("scores", "recommendations"):
                path = output / f"{name}.parquet"
                os.replace(output / f"{name}.parquet.partial", path)
                artifacts[name] = {"path": path.name, "sha256": file_sha256(path)}
            document = {
                "format": "music-recsys-full-result-v1",
                "status": "succeeded",
                "phase": phase,
                "model": config.model,
                "model_parameters": config.parameters,
                "popularity_basis": basis if config.model == "popularity" else None,
                "population": counts,
                "primary": asdict(config.evaluation),
                "accuracy": rows,
                "diversity": beyond,
                "efficiency": efficiency,
                "artifacts": artifacts,
                "definitions": evaluation_definitions(
                    config, factor_model=factor_model, itemcf=itemcf, basis=basis, use_gpu=use_gpu
                ),
            }
            render(config, document)
            if phase == "test":
                for reference in validation_evidence.values():
                    if file_sha256(reference["path"]) != reference["sha256"]:
                        raise ValueError("Validation evidence changed during Test")
            if export_path is not None:
                from src.infrastructure.models.artifacts import export_model
                from src.infrastructure.recommenders.full_popularity import FullPopularity

                deployed = factor_model if factor_model is not None else itemcf
                if deployed is None:
                    deployed = FullPopularity(paths["learning"], basis=basis)
                document["serving_model"] = {
                    "version_id": model_version,
                    "manifest_sha256": export_model(
                        deployed, export_path, version_id=model_version
                    ),
                    "path": str(Path(export_path).resolve()),
                }
            atomic_json(output / "result.json", document)
            progress(f"SUCCEEDED: {counts['users']:,} users; results at {output}")
    return output


def evaluation_definitions(
    config, *, factor_model=None, itemcf=None, basis="play_count", use_gpu=False
):
    """Shared scientific definitions for independent and shared-neighbor evaluations."""
    return {
        "population": "all train-observed users; no future activity selection",
        "accuracy": "equal user mean; users without positives excluded separately for each task/window",
        "ranking": (
            (
                "tag TF-IDF profile cosine"
                if config.model == "content"
                else "BM25-weighted item-neighbor sum"
                if config.model == "bm25"
                else "binary history times learned dense item coefficients; zero diagonal"
                if config.model == "ease"
                else f"{config.model.upper()} latent score"
            )
            + "; ties by training plays then item ID"
            if factor_model is not None and config.model not in {"popularity", "itemcf"}
            else f"training {basis} descending, item ID ascending"
            if itemcf is None
            else "binary user-item similarity; sum of history-item top-neighbor weights; ties by training plays then item ID"
        )
        + "; new excludes history; repeat restricts to history",
        "gain": "log2(1+play_count)+day_weight*log2(1+active_days)",
        "coverage": "unique recommended items / union of task candidates",
        "novelty": "-log2((distinct learning users for item+1)/(learning users+2))",
        "ild": "mean pairwise cosine distance; all returned valid tracks require nonzero tags",
        "ci": "user bootstrap, 2000 resamples, seed 42, primary window/weight only",
        "details": "scores: K array follows config ks; day_weight=-1 means not applicable; absent user-condition has no positive truth; recommendations: all users and both tasks",
        "latency": (
            "GPU batched ranking including transfer and stable sorting; amortized per user/task; not single-request latency"
            if use_gpu
            else "optimized deterministic ranking retrieval, excludes I/O, training and scoring; cannot compare to old full-sort implementation without accounting for implementation change"
        ),
        "usage": "unmeasured is null",
    }


def render(config, document, *, observations=None):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import (
        accuracy_figures,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )
    from src.interfaces.notebook.listening_analysis import observation_distribution

    frame = pd.DataFrame(document["accuracy"])
    primary = frame[
        (frame.window_days == config.evaluation.primary_window_days)
        & (
            (frame.metric != "graded_ndcg")
            | (frame.day_weight == config.evaluation.primary_day_weight)
        )
    ]
    beyond = pd.DataFrame(document["diversity"])
    efficiency = pd.DataFrame([document["efficiency"]])
    if observations is None:
        observations = observation_distribution(config, phase=document.get("phase", "validation"))
    document["observations"] = json.loads(observations.to_json(orient="records"))
    figures = accuracy_figures(primary)
    figures.update(diversity_figures(beyond))
    figures.update(efficiency_figures(efficiency))
    figures.update(observation_figures(observations))
    from src.interfaces.notebook.evaluation_figures import accuracy_heatmaps

    for name, figure in figures.items():
        figure.savefig(config.output / f"{name}.png", dpi=300)
        plt.close(figure)
    heatmaps = accuracy_heatmaps(frame, config.evaluation.primary_day_weight)
    for name, figure in heatmaps.items():
        figure.savefig(config.output / f"{name}.png", dpi=300)
        plt.close(figure)
    document["figures"] = [f"{name}.png" for name in [*figures, *heatmaps]]
    from src.infrastructure.provenance import source_hash
    from src.interfaces.notebook.evaluation_figures import prune_generated_figures

    prune_generated_figures(config.output, document["figures"])
    document["render_source_sha256"] = source_hash()
    tables = [
        frame.rename(columns={"mean": "value"}).assign(aspect="accuracy"),
        beyond.melt(
            id_vars=["task", "k"],
            value_vars=["ild", "catalog_coverage", "novelty"],
            var_name="metric",
            value_name="value",
        ).assign(aspect="diversity"),
        efficiency.melt(id_vars=["model"], var_name="metric", value_name="value").assign(
            aspect="efficiency"
        ),
        observations.rename(columns={"observation": "metric", "pairs": "value"}).assign(
            aspect="observations"
        ),
    ]
    pd.concat(tables, ignore_index=True).to_csv(
        config.output / "metrics.csv", index=False, encoding="utf-8-sig"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--export-model", type=Path)
    parser.add_argument("--model-version")
    parser.add_argument("--phase", choices=("validation", "test"), default="validation")
    parser.add_argument("--gpu-popularity", action="store_true")
    parser.add_argument("--gpu-itemcf", action="store_true")
    parser.add_argument("--als-backend", choices=("cpu", "cuda"))
    parser.add_argument("--ease-memory-budget-mb", type=float)
    args = parser.parse_args()
    print(
        run(
            args.config,
            export_path=args.export_model,
            model_version=args.model_version,
            phase=args.phase,
            gpu_popularity=args.gpu_popularity,
            gpu_itemcf=args.gpu_itemcf,
            als_backend=args.als_backend,
            ease_memory_budget_mb=args.ease_memory_budget_mb,
        )
    )


if __name__ == "__main__":
    main()
