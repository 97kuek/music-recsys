"""Evaluate one explicit BM25 k1/b group using exact prefixes of a shared neighbor table."""

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.sensitivity import conditions
from src.paths import PROJECT_ROOT


def run(path, k1, b):
    import cupy as cp
    import numpy as np

    from src.bootstrap import create_full_report
    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.evaluation.itemcf_sensitivity import prefix_models, score_prefixes
    from src.infrastructure.provenance import source_hash
    from src.infrastructure.recommenders.full_bm25 import FullBM25
    from src.infrastructure.research.evaluation import evaluation_definitions
    from src.interfaces.notebook.listening_analysis import observation_distribution

    parent = load_study_config(path)
    configs = [
        t
        for _, t in conditions(parent)
        if t.parameters.get("k1") == k1 and t.parameters.get("b") == b
    ]
    if parent.model != "bm25" or not configs or not parent.parameters.get("use_gpu"):
        raise ValueError("Expected an explicit GPU BM25 neighbor group")
    if any(c.output.exists() for c in configs):
        raise FileExistsError("Shared-neighbor outputs already exist; preserved without overwrite")
    manifest, paths, counts = validate_bundle(parent.validation_dataset)
    if manifest["phase"] != "validation":
        raise ValueError("Shared-neighbor exploration must use Validation")
    features = load_features(
        parent.features,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    observed = observation_distribution(parent)
    owner = max(c.parameters["neighbors"] for c in configs)
    base = FullBM25(paths["learning"], neighbors=owner, k1=k1, b=b, use_gpu=True)
    models = prefix_models(base, [c.parameters["neighbors"] for c in configs])
    item_users = dict(
        zip(
            base.items,
            np.asarray(base.histories.sum(axis=0)).ravel().astype(int).tolist(),
            strict=True,
        )
    )
    sources = []
    for c, model in zip(configs, models, strict=True):
        c.output.mkdir(parents=True)
        saved = dict(
            format="music-recsys-full-config-v1",
            settings=json.loads(json.dumps(asdict(c), default=str)),
            manifest=manifest,
            features_sha256=file_sha256(parent.features),
            source_sha256=source_hash(),
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            population=counts,
            training_cache_hit=c.parameters["neighbors"] != owner,
            phase="validation",
            input_dataset=str(parent.validation_dataset),
            execution=dict(
                model.execution,
                evaluation_device="cuda",
                evaluation_batch_size=128,
                metric_dtype="float64",
                cupy=cp.__version__,
                numpy=np.__version__,
                seed=c.seeds[0],
                threads={
                    k: os.environ.get(k)
                    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
                },
                neighbor_table_owner=owner,
                neighbor_table_width=model.targets.shape[1],
                timing=f"One exact top-{owner} table per k1/b; all n use its prefixes. training_seconds is shared table construction, not an independent fit per condition. History mask time divided equally; condition evaluation excludes shared input streaming.",
            ),
        )
        atomic_json(c.output / "config.json", saved)
        sources.append(saved)
    started = time.perf_counter()
    values = score_prefixes(configs, models, paths, counts, features, item_users, print)
    for c, model, saved, (accuracy, diversity, efficiency) in zip(
        configs, models, sources, values, strict=True
    ):
        with study_lock(c):
            artifacts = {}
            for name in ("scores", "recommendations"):
                target = c.output / f"{name}.parquet"
                os.replace(c.output / f"{name}.parquet.partial", target)
                artifacts[name] = dict(path=target.name, sha256=file_sha256(target))
            document = dict(
                format="music-recsys-full-result-v1",
                status="succeeded",
                phase="validation",
                model="bm25",
                model_parameters=c.parameters,
                popularity_basis=None,
                population=counts,
                primary=asdict(c.evaluation),
                accuracy=accuracy,
                diversity=diversity,
                efficiency=efficiency,
                artifacts=artifacts,
                definitions=evaluation_definitions(c, factor_model=model, use_gpu=True),
            )
            create_full_report().write(c, document, observations=observed)
            atomic_json(c.output / "result.json", document)
            (c.output / "run.log").write_text(
                f"SUCCEEDED: {counts['users']} users; shared top-{owner} table; k1={k1:g}, b={b:g}; timing metadata in config.json; group scoring/render seconds={time.perf_counter() - started:.3f}\n"
            )
    return [c.output for c in configs]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--k1", required=True, type=float)
    parser.add_argument("--b", required=True, type=float)
    args = parser.parse_args()
    run(args.config, args.k1, args.b)


if __name__ == "__main__":
    main()
