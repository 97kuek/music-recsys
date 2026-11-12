"""Immutable same-union EASE/RRF control, verified from frozen top-100 retrieval."""

import os
import time
from pathlib import Path

import numpy as np

from backend.domain.recommendation.pipeline import FusionSpec
from backend.infrastructure.datasets.full_io import DetailWriter
from backend.infrastructure.datasets.listening_data import file_sha256, read_bundle
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.recommenders.pipeline import BatchedPipeline
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import read
from backend.infrastructure.research.pool_strata import training_bands
from backend.infrastructure.research.results import read_result, verify_artifacts
from backend.infrastructure.research.store import atomic_json
from backend.infrastructure.research.union_strata import summarize
from backend.infrastructure.research.union_verify import collect
from backend.paths import PROJECT_ROOT


def comparison_tables(root):
    import pandas as pd

    names = {"ease": "ease-union", "ease-bpr-rrf": "rrf"}
    frame = pd.read_csv(root / "metrics.csv")
    frame = frame[frame.condition.isin(names)].copy()
    # Ranking identity transfers quality, not the unexecuted rescoring latency.
    derived_cost = (frame.condition == "ease") & (frame.aspect == "efficiency")
    for field in ("mean", "ci_low", "ci_high", "seed_sd"):
        frame.loc[derived_cost, field] = np.nan
    frame["measurement"] = "saved results; ranking equivalence verified"
    frame.loc[derived_cost, "measurement"] = "union rescoring not timed"
    frame["condition"] = frame.condition.map(names)
    frame["model"] = frame.condition
    paired = pd.read_csv(root / "paired.csv")
    paired = paired[(paired.model_left == "ease") & (paired.model_right == "ease-bpr-rrf")].copy()
    paired["model_left"], paired["model_right"] = "ease-union", "rrf"
    common = pd.read_csv(root / "ild.csv")
    common = common[(common.pair == "ease-bpr") & common.condition.isin(names)].copy()
    common["condition"] = common.condition.map(names)

    def clean(frame):
        return frame.astype(object).where(frame.notna(), None).to_dict(orient="records")

    return clean(frame), clean(paired), clean(common)


def run(config, *, xp, execution, save_figures):
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = config.output
    output, partial = root / "union-control", root / "union-control.partial"
    if output.exists() or partial.exists():
        raise FileExistsError("Existing or partial same-pool analysis must be preserved")
    started = time.perf_counter()
    processing = source_hash()
    result = read_result(config)
    if result["status"] != "succeeded" or result["phase"] != "validation":
        raise ValueError("Completed Validation required")
    prior = root / "pool-strata"
    prior_result, prior_config = read(prior / "result.json"), read(prior / "config.json")
    if prior_result["status"] != "succeeded" or prior_result["phase"] != "validation":
        raise ValueError("Completed pool-strata evidence required")
    verify_artifacts(prior, prior_result["artifacts"])
    evidence = dict(prior_config["evidence"])
    # The successful input validator's exact files must still match before reuse.
    for path, digest in evidence.items():
        if file_sha256(path) != digest:
            raise ValueError("Frozen input evidence changed")

    def record(path):
        path = Path(path).resolve()
        evidence[str(path)] = file_sha256(path)

    for path in (
        prior / "result.json",
        prior / "config.json",
        config.path,
        PROJECT_ROOT / "uv.lock",
    ):
        record(path)
    verify_artifacts(root, result["artifacts"])
    for entry in result["artifacts"].values():
        record(root / entry["path"])
    saved = read(root / "config.json")
    manifest, paths = read_bundle(config.validation_dataset)
    if manifest != saved["manifest"] or result["population"] != prior_result["population"]:
        raise ValueError("Input population differs")
    users, catalog, history, popularity, bands = training_bands(paths["learning"])
    items, plays = [r[0] for r in catalog], np.array([r[2] for r in catalog])
    if len(users) != result["population"]["users"] or bands != prior_result["strata"]:
        raise ValueError("Strata or population differs")
    table = pq.read_table(root / "catalog.parquet")
    if table["item_id"].to_pylist() != items or table["plays"].to_pylist() != plays.tolist():
        raise ValueError("Catalog differs")
    runs = {}
    for name in ("ease", *(f"ease-bpr-rrf-s{s}" for s in (42, 43, 44))):
        directory = root / name
        run_result, run_config = read(directory / "result.json"), read(directory / "config.json")
        signature = {
            k: saved[k] for k in ("settings", "manifest", "features_sha256", "source_sha256")
        }
        if run_config["signature"] != signature or run_result["status"] != "succeeded":
            raise ValueError("Recommendation provenance differs")
        verify_artifacts(directory, run_result["artifacts"])
        for entry in run_result["artifacts"].values():
            record(directory / entry["path"])
        record(directory / "result.json")
        record(directory / "config.json")
        runs[name] = run_result
    spec = FusionSpec(**{**saved["pipeline"], "weights": tuple(saved["pipeline"]["weights"])})
    if spec.top_m < max(config.evaluation.ks) or max(config.evaluation.ks) > 20:
        raise ValueError("Top-K identity requires K <= saved source budget and K <= 20")
    engine = BatchedPipeline({}, items, plays, spec, xp=xp)
    execution = dict(
        execution,
        dtype="float64",
        solver="frozen ranking identity + set membership",
        training=False,
        seed=42,
        bootstrap=2000,
        seeds=[42, 43, 44],
    )
    partial.mkdir()
    atomic_json(
        partial / "config.json",
        dict(
            input_config=str(config.path),
            evidence=evidence,
            source_sha256=processing,
            execution=execution,
            strata=bands,
            ks=list(config.evaluation.ks),
            selection="not_selected",
            phase="validation",
        ),
    )
    detail_schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("window_days", pa.int64()),
            ("stratum_kind", pa.string()),
            ("stratum", pa.string()),
            *[(name, pa.list_(pa.float64())) for name in ("ease", "rrf", "added")],
        ]
    )
    proof_schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("pool_count", pa.int64()),
            ("ease_item_ids", pa.list_(pa.string())),
            ("rrf_item_ids", pa.list_(pa.string())),
        ]
    )
    seeds, hashes = [], {}
    with (partial / "run.log").open("x") as log:

        def progress(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        for seed in (42, 43, 44):
            details = DetailWriter(partial / f"scores-s{seed}.parquet", schema=detail_schema)
            proof = DetailWriter(partial / f"recommendations-s{seed}.parquet", schema=proof_schema)
            try:
                values, digest = collect(
                    root,
                    seed,
                    users,
                    items,
                    plays,
                    history,
                    popularity,
                    paths["observations"],
                    config.evaluation,
                    engine,
                    details,
                    proof,
                    progress,
                )
            finally:
                details.close()
                proof.close()
            if digest != runs[f"ease-bpr-rrf-s{seed}"]["pool_sha256"]:
                raise ValueError("Reconstructed candidate pool hash differs from frozen RRF")
            seeds.append(values)
            hashes[str(seed)] = digest
            progress(f"Verified exact pool and Top20 for seed {seed}")
        strata = summarize(seeds, history, config.evaluation.ks, xp=xp)
        rows, paired, common = comparison_tables(root)
        # Independent ranked hit counting must reproduce saved global Recall for every K/window.
        reference = {
            (
                r["condition"],
                "new" if r["task"] == "discovery" else r["task"],
                r["window_days"],
                r["k"],
            ): r
            for r in rows
            if r["metric"] in ("recall", "repeat_recall")
        }
        errors = []
        for row in strata:
            if row["stratum_kind"] == "all" and row["condition"] in ("ease-union", "rrf"):
                original = reference[row["condition"], row["task"], row["window_days"], row["k"]]
                if row["n_evaluated"] != original["n_evaluated"]:
                    raise ValueError("Recall evaluated population differs")
                error = abs(row["mean"] - original["mean"]) if row["mean"] is not None else 0
                if error > 1e-12:
                    raise ValueError("Ranked Recall differs from original results")
                errors.append(error)
        for name, values in (
            ("metrics", rows),
            ("paired", paired),
            ("ild", common),
            ("strata", strata),
        ):
            write_csv(partial / f"{name}.csv", values)
        figures = save_figures(partial, rows, common, strata)
        for path, digest in evidence.items():
            if file_sha256(path) != digest:
                raise ValueError("Input changed during analysis")
        if source_hash() != processing:
            raise ValueError("Processing source changed during analysis")
        progress("SUCCEEDED: all users, three seeds, exact frozen pools and Top20")
    artifacts = {
        p.name: dict(path=p.name, sha256=file_sha256(p)) for p in partial.iterdir() if p.is_file()
    }
    atomic_json(
        partial / "result.json",
        dict(
            format="music-recsys-union-control-v1",
            status="succeeded",
            phase="validation",
            selection="not_selected",
            population=result["population"],
            strata=bands,
            execution=execution,
            elapsed_seconds=time.perf_counter() - started,
            artifacts=artifacts,
            pool_sha256=hashes,
            figures=figures,
            recall_checks=len(errors),
            max_recall_error=max(errors),
            observations=result["observations"],
            definitions=dict(
                identity="TopK(EASE100 union BPR100, frozen EASE scores/ties) equals EASE[:K] for K<=100; verified against saved recommendations for all users/seeds",
                scoring="No refit or missing-score imputation; original full-catalog top-200 retrieval certifies the ranking prefix; no full union score matrix materialized",
                strata="History and popularity separately, not crossed; popularity restricts both truth and hits; original RRF positions retained",
                added="rrf-bpr-only Recall counts hits absent from EASE100, with the same full band truth denominator",
                uncertainty="Three seeds averaged per user; 2000 paired user bootstrap samples seed42 at 90d/K20; exploratory 95% intervals without multiplicity correction",
                ild="Original common EASE/BPR-family intersection (including BPR and MMR), unchanged for both controls; not a newly expanded pair-only population",
                efficiency="RRF costs reused from frozen run; EASE union rescoring costs unmeasured/null; diagnostic elapsed time is not model latency",
                provenance="Original metrics, paired intervals and distributions reused only after exact ranking and pool verification; individual full accuracy scores referenced by evidence",
            ),
        ),
    )
    if output.exists():
        raise FileExistsError(output)
    os.rename(partial, output)
    return output
