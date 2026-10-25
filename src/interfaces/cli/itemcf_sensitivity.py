"""Full-user Validation sensitivity for ItemCF similarities and neighbor counts."""

import argparse
import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.paths import PROJECT_ROOT


def trials(parent, similarity):
    return [
        replace(
            parent,
            name=f"itemcf-{similarity}-{n}",
            output=parent.output / similarity / str(n) / "validation",
            parameters=dict(similarity=similarity, neighbors=n),
            grid={},
            max_trials=1,
        )
        for n in parent.grid["neighbors"]
    ]


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _source(config, manifest, counts, features_hash, model):
    import cupy as cp
    import numpy as np

    from src.infrastructure.provenance import source_hash

    return dict(
        format="music-recsys-full-config-v1",
        settings=json.loads(json.dumps(asdict(config), default=str)),
        manifest=manifest,
        population=counts,
        features_sha256=features_hash,
        source_sha256=source_hash(),
        dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
        training_cache_hit=model.cache_hit,
        phase="validation",
        input_dataset=str(config.validation_dataset),
        execution=dict(
            model.execution,
            evaluation_device="cuda",
            evaluation_batch_size=128,
            metric_dtype="float64",
            cupy=cp.__version__,
            numpy=np.__version__,
            seed=config.seeds[0],
            threads={
                k: os.environ.get(k)
                for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
            },
            neighbor_table_width=model.targets.shape[1],
            neighbor_table_owner=max(config.grid.get("neighbors", [500])),
            timing="Neighbor table shared by all n; history-mask time split equally; sequential condition work excludes shared input streaming; training time is table construction, not an independent fit per n",
        ),
    )


def _finish(trial, saved, values, observations):
    from src.interfaces.cli.evaluate import render

    rows, beyond, efficiency = values
    artifacts = {}
    for name in ("scores", "recommendations"):
        path = trial.output / f"{name}.parquet"
        os.replace(trial.output / f"{name}.parquet.partial", path)
        artifacts[name] = dict(path=path.name, sha256=file_sha256(path))
    document = dict(
        format="music-recsys-full-result-v1",
        status="succeeded",
        phase="validation",
        model="itemcf",
        model_parameters=trial.parameters,
        population=saved["population"],
        primary=asdict(trial.evaluation),
        accuracy=rows,
        diversity=beyond,
        efficiency=efficiency,
        artifacts=artifacts,
        definitions=dict(
            population="all train-observed users; no future activity selection",
            accuracy="equal user mean; no-positive users excluded by task/window",
            similarity="Binary presence over all train users; Pearson is phi; undefined/zero/negative edges excluded; positive top-n by similarity then item ID",
            ranking="binary history times directed top-n item similarities; ties by training play count then item ID; new excludes history; repeat restricts to history",
            gain="log2(1+play_count)+day_weight*log2(1+active_days)",
            coverage="unique recommended items / union of task candidates",
            novelty="-log2((distinct learning users for item+1)/(learning users+2))",
            ild="mean pairwise cosine distance; nonzero tags required for every returned item",
            ci="2000 user bootstrap resamples, seed42; primary window/weight; not training-seed uncertainty",
            latency="sequential GPU batched ranking; history-mask cost divided among prefixes; amortized per user/task; not independent single-request latency",
            usage="unmeasured is null",
        ),
    )
    render(trial, document, observations=observations)
    atomic_json(trial.output / "result.json", document)
    (trial.output / "run.log").write_text(
        "SUCCEEDED full population; shared top-500 table; all four aspects and observation distributions; detailed execution in ../../../sensitivity/run.log\n",
        encoding="utf-8",
    )


def run(path, *, summarize=False):
    import cupy as cp

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.evaluation.itemcf_sensitivity import prefix_models, score_prefixes
    from src.infrastructure.provenance import source_hash
    from src.infrastructure.recommenders.full_itemcf import FullItemCF
    from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu
    from src.interfaces.notebook.listening_analysis import observation_distribution

    parent = load_study_config(path)
    if (
        parent.model != "itemcf"
        or parent.protocol != "listening-v2"
        or set(parent.grid) != {"similarity", "neighbors"}
        or parent.task is not None
        or parent.metric is not None
    ):
        raise ValueError("Expected an unselected listening-v2 ItemCF similarity/neighbors grid")
    if len(parent.grid["similarity"]) * len(parent.grid["neighbors"]) > parent.max_trials:
        raise ValueError("Too many conditions")
    if any(s not in {"cosine", "jaccard", "pearson"} for s in parent.grid["similarity"]) or any(
        type(n) is not int or n < 1 for n in parent.grid["neighbors"]
    ):
        raise ValueError("Unsupported similarities or neighbor counts")
    report = parent.output / "sensitivity"
    if (report / "result.json").exists():
        raise FileExistsError("Completed sensitivity results are immutable")
    print("GPU preflight: " + require_idle_gpu(), flush=True)
    with study_lock(replace(parent, output=report)):
        if summarize:
            return publish(parent)
        print("Validating the full Validation bundle once", flush=True)
        manifest, paths, counts = validate_bundle(parent.validation_dataset)
        if manifest["phase"] != "validation":
            raise ValueError("Exploration requires Validation")
        features = load_features(
            parent.features,
            dataset_id=manifest["dataset_id"],
            snapshot_id=manifest["source"]["snapshot_id"],
        )
        feature_hash = file_sha256(parent.features)
        planned = dict(
            format="music-recsys-itemcf-sensitivity-config-v1",
            phase="validation",
            parent=json.loads(json.dumps(asdict(parent), default=str)),
            manifest=manifest,
            population=counts,
            features_sha256=feature_hash,
            source_sha256=source_hash(),
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            report=dict(window_days=90, k=20),
            policy="Additional exploration after existing Test; preserve Test and prior Validation; no selection",
            pearson=dict(
                population="all learning users",
                values="binary presence 0/1",
                definition="phi=(N*shared-left*right)/sqrt(left*(N-left)*right*(N-right))",
                edges="positive only; constant columns have undefined correlation and no edges",
            ),
        )
        if (report / "config.json").exists():
            if _read(report / "config.json") != planned:
                raise ValueError("Saved sensitivity plan differs")
        else:
            atomic_json(report / "config.json", planned)
        observed = observation_distribution(parent)
        cache = (
            parent.validation_dataset.parents[2]
            / "processed/itemcf"
            / file_sha256(paths["learning"])
        )
        with (report / "run.log").open("a", encoding="utf-8") as log:

            def progress(message):
                print(message, flush=True)
                log.write(message + "\n")
                log.flush()

            for similarity in parent.grid["similarity"]:
                configs = trials(parent, similarity)
                if all((c.output / "result.json").exists() for c in configs):
                    progress(
                        f"Reusing completed {similarity} group; all evidence verified before publication"
                    )
                    continue
                if any(c.output.exists() for c in configs):
                    raise FileExistsError("Partial conditions are preserved; do not overwrite")
                progress(f"Fitting {similarity} top-{max(parent.grid['neighbors'])} neighbors")
                base = FullItemCF(
                    paths["learning"],
                    similarity=similarity,
                    neighbors=max(parent.grid["neighbors"]),
                    use_gpu=True,
                    cache_dir=cache,
                    progress=progress,
                )
                models = prefix_models(base, parent.grid["neighbors"])
                saved = []
                for c, model in zip(configs, models, strict=True):
                    c.output.mkdir(parents=True)
                    source = _source(c, manifest, counts, feature_hash, model)
                    source["execution"]["neighbor_table_owner"] = max(parent.grid["neighbors"])
                    atomic_json(c.output / "config.json", source)
                    saved.append(source)
                item_users = dict(zip(base.items, np_item_users(base), strict=True))
                started = time.perf_counter()
                values = score_prefixes(
                    configs, models, paths, counts, features, item_users, progress
                )
                for c, source, value in zip(configs, saved, values, strict=True):
                    _finish(c, source, value, observed)
                progress(
                    f"SUCCEEDED {similarity}: {len(configs)} conditions; group evaluation/render seconds={time.perf_counter() - started:.3f}"
                )
                del models, base, values
                cp.get_default_memory_pool().free_all_blocks()
            progress("Summarizing all completed conditions")
            output = publish(parent)
            progress("SUCCEEDED sensitivity report: " + str(output))
            return output


def np_item_users(model):
    import numpy as np

    return np.asarray(model.histories.sum(axis=0)).ravel().astype(int).tolist()


def publish(parent):
    import cupy as cp
    import pandas as pd

    from src.infrastructure.provenance import source_hash
    from src.infrastructure.research.als_report import score_audit, write_csv
    from src.infrastructure.research.complementarity import paired_score_differences
    from src.interfaces.cli.catalog import metric_rows
    from src.interfaces.notebook.itemcf_heatmaps import save_heatmaps

    report = parent.output / "sensitivity"
    planned = _read(report / "config.json")
    if planned["source_sha256"] != source_hash() or planned[
        "dependency_lock_sha256"
    ] != file_sha256(PROJECT_ROOT / "uv.lock"):
        raise ValueError("Processing source/lock changed after plan freeze")
    rows, accuracy, references, sources, verified = [], [], [], {}, 0
    for similarity in parent.grid["similarity"]:
        for c in trials(parent, similarity):
            result, saved = _read(c.output / "result.json"), _read(c.output / "config.json")
            expected = json.loads(json.dumps(asdict(c), default=str))
            if (
                result.get("status") != "succeeded"
                or result.get("phase") != "validation"
                or saved["settings"] != expected
                or saved["manifest"] != planned["manifest"]
                or saved["features_sha256"] != planned["features_sha256"]
                or saved["source_sha256"] != planned["source_sha256"]
                or result["population"] != planned["population"]
                or result["primary"] != expected["evaluation"]
            ):
                raise ValueError("Saved condition does not match the frozen comparison")
            verify_artifacts(c.output, result["artifacts"])
            meta = dict(similarity=similarity, neighbors=c.parameters["neighbors"])
            verified += score_audit(
                dict(result=result, result_path=str(c.output / "result.json")), cp
            )
            rows.extend(dict(meta, **r) for r in metric_rows(result))
            accuracy.extend(dict(meta, **r) for r in result["accuracy"])
            name = f"{similarity}-{c.parameters['neighbors']}"
            sources[name] = dict(
                scores=c.output / result["artifacts"]["scores"]["path"],
                population=result["population"],
            )
            references.append(
                dict(
                    meta,
                    config=str(c.output / "config.json"),
                    config_sha256=file_sha256(c.output / "config.json"),
                    result=str(c.output / "result.json"),
                    result_sha256=file_sha256(c.output / "result.json"),
                )
            )
    paired = paired_score_differences(
        sources,
        list(parent.evaluation.ks),
        windows=parent.evaluation.window_days,
        xp=cp,
        ci_ks=(20,),
    )
    for r in references:
        for name in ("config", "result"):
            if file_sha256(r[name]) != r[name + "_sha256"]:
                raise ValueError("Source changed during aggregation")
    for name in (
        "metrics.csv",
        "paired.csv",
        "recall.png",
        "recall.pdf",
        "repeat_recall.png",
        "repeat_recall.pdf",
    ):
        if (report / name).exists():
            raise FileExistsError("Partial report evidence is preserved")
    write_csv(
        report / "metrics.csv",
        rows,
        ("similarity", "neighbors", "aspect", "task", "metric", "k", "mean"),
    )
    write_csv(
        report / "paired.csv",
        paired,
        ("model_left", "model_right", "task", "metric", "k", "mean", "ci_low", "ci_high"),
    )
    figures = save_heatmaps(
        report, pd.DataFrame(accuracy), parent.grid["similarity"], parent.grid["neighbors"]
    )
    artifacts = {
        p.name: dict(path=p.name, sha256=file_sha256(p))
        for p in report.iterdir()
        if p.is_file() and p.name not in {"run.log", ".lock"}
    }
    atomic_json(
        report / "result.json",
        dict(
            format="music-recsys-itemcf-sensitivity-v1",
            status="succeeded",
            phase="validation",
            selection="not_selected",
            population=planned["population"],
            conditions=len(references),
            verified_accuracy_rows=verified,
            sources=references,
            figures=figures,
            artifacts=artifacts,
            inference="Unadjusted paired user bootstrap intervals at 90d/K20; no model selected; not an independent final Test",
        ),
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    print(run(args.config, summarize=args.summarize))


if __name__ == "__main__":
    main()
