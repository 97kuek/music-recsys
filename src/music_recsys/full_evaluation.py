"""全学習ユーザーのPopularity / ItemCF / ALS / BPR Validation / 集約履歴を順次読み、個別結果をParquet保存。"""

import argparse
import json
import os
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import numpy as np

from music_recsys.experiment_store import atomic_json
from music_recsys.full_io import DetailWriter, user_rows
from music_recsys.listening_data import file_sha256, validate_bundle

__all__ = ["DetailWriter", "user_rows", "run", "render"]


def run(config_path, *, model_cache=None):
    import duckdb

    from music_recsys.evaluation.benchmark import _source_sha256
    from music_recsys.experiment_search import load_study_config, study_lock
    from music_recsys.features import load_features

    config = load_study_config(config_path)
    allowed = {
        "popularity": {"basis"},
        "itemcf": {"neighbors", "similarity"},
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
            "full evaluation requires listening-v2 popularity/itemcf/als/bpr with fixed parameters"
        )
    if config.model in {"als", "bpr"} and len(config.seeds) != 1:
        raise ValueError("full factor model requires exactly one seed per run")
    from music_recsys.recommenders.popularity import validate_basis

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

            progress("Validating full Validation bundle")
            manifest, paths, counts = validate_bundle(config.validation_dataset)
            if manifest["phase"] != "validation":
                raise ValueError("Validation only; Test is not opened")
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
                from music_recsys.recommenders.full_itemcf import FullItemCF

                itemcf = FullItemCF(
                    paths["learning"], progress=progress, cache_dir=model_cache, **config.parameters
                )
            factor_model = None
            if config.model in {"als", "bpr"}:
                from music_recsys.recommenders.full_als import FullALS
                from music_recsys.recommenders.full_bpr import FullBPR

                factor_type = FullALS if config.model == "als" else FullBPR
                factor_model = factor_type(
                    paths["learning"], seed=config.seeds[0], progress=progress, **config.parameters
                )
            training_seconds = (
                itemcf.training_seconds if itemcf is not None else time.perf_counter() - started
            )
            if factor_model is not None:
                training_seconds = factor_model.training_seconds
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
                    "source_sha256": _source_sha256(),
                    "dependency_lock_sha256": file_sha256(
                        Path(__file__).resolve().parents[2] / "uv.lock"
                    ),
                    "execution": (
                        {
                            "solver": "exact normal equations float64",
                            **getattr(factor_model, "execution", {}),
                            "evaluation_device": "cuda"
                            if config.parameters.get("use_gpu", False)
                            else "cpu",
                            "evaluation_batch_size": 128
                            if config.parameters.get("use_gpu", False)
                            else 1,
                            **(
                                {"cupy": version("cupy-cuda12x"), "metric_dtype": "float64"}
                                if config.parameters.get("use_gpu", False)
                                else {}
                            ),
                            "seed": config.seeds[0],
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
                        if factor_model is not None
                        else f"deterministic {config.model} once; no seed-dependent computation"
                    ),
                    "population": counts,
                    "training_cache_hit": itemcf.cache_hit if itemcf is not None else False,
                },
            )
            if config.parameters.get("use_gpu", False):
                from music_recsys.gpu_evaluation import score_gpu as scorer
            else:
                from music_recsys.full_scoring import score_cpu as scorer
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
                "phase": "validation",
                "model": config.model,
                "model_parameters": config.parameters,
                "popularity_basis": basis if config.model == "popularity" else None,
                "population": counts,
                "primary": asdict(config.evaluation),
                "accuracy": rows,
                "diversity": beyond,
                "efficiency": efficiency,
                "artifacts": artifacts,
                "definitions": {
                    "population": "all train-observed users; no future activity selection",
                    "accuracy": "equal user mean; users without positives excluded separately for each task/window",
                    "ranking": (
                        f"{config.model.upper()} latent score; ties by training plays then item ID"
                        if factor_model is not None
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
                        if config.parameters.get("use_gpu", False)
                        else "optimized deterministic ranking retrieval, excludes I/O, training and scoring; cannot compare to old full-sort implementation without accounting for implementation change"
                    ),
                    "usage": "unmeasured is null",
                },
            }
            render(config, document)
            atomic_json(output / "result.json", document)
            progress(f"SUCCEEDED: {counts['users']:,} users; results at {output}")
    return output


def render(config, document):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from music_recsys.evaluation_figures import (
        accuracy_figures,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )
    from music_recsys.listening_analysis import observation_distribution

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
    observations = observation_distribution(config)
    document["observations"] = json.loads(observations.to_json(orient="records"))
    figures = accuracy_figures(primary)
    figures.update(diversity_figures(beyond))
    figures.update(efficiency_figures(efficiency))
    figures.update(observation_figures(observations))
    from music_recsys.evaluation_figures import accuracy_heatmaps

    for name, figure in figures.items():
        figure.savefig(config.output / f"{name}.png", dpi=160)
        plt.close(figure)
    heatmaps = accuracy_heatmaps(frame, config.evaluation.primary_day_weight)
    for name, figure in heatmaps.items():
        figure.savefig(config.output / f"{name}.png", dpi=160)
        plt.close(figure)
    document["figures"] = [f"{name}.png" for name in [*figures, *heatmaps]]
    from music_recsys.evaluation.benchmark import _source_sha256
    from music_recsys.evaluation_figures import prune_generated_figures

    prune_generated_figures(config.output, document["figures"])
    document["render_source_sha256"] = _source_sha256()
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
    args = parser.parse_args()
    print(run(args.config))


if __name__ == "__main__":
    main()
