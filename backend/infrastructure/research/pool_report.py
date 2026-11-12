"""Verify frozen Hybrid inputs and atomically publish a separate pool diagnostic."""

import os
import time
from pathlib import Path

from backend.infrastructure.datasets.full_io import DetailWriter
from backend.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import read, verify_references
from backend.infrastructure.research.pool_strata import collect, summarize, training_bands
from backend.infrastructure.research.results import read_result, verify_artifacts
from backend.infrastructure.research.store import atomic_json
from backend.paths import PROJECT_ROOT


def run(config, *, xp, execution, save_figures):
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = config.output
    output = root / "pool-strata"
    partial = root / "pool-strata.partial"
    if output.exists() or partial.exists():
        raise FileExistsError("Pool analysis already exists or is incomplete; preserve it")
    started = time.perf_counter()
    processing_hash = source_hash()
    evidence = {}

    def record(path):
        path = Path(path).resolve()
        evidence[str(path)] = file_sha256(path)

    record(PROJECT_ROOT / "uv.lock")
    record(config.path)
    record(root / "config.json")
    record(root / "result.json")
    saved = read(root / "config.json")
    result = read_result(config)
    if result["status"] != "succeeded" or result["phase"] != "validation":
        raise ValueError("A completed Validation is required")
    verify_artifacts(root, result["artifacts"])
    for artifact in result["artifacts"].values():
        record(root / artifact["path"])
    # Large validation joins otherwise spill onto the nearly full artifact volume.
    validation_memory_limit = "16GB"
    manifest, paths, counts = validate_bundle(
        config.validation_dataset, memory_limit=validation_memory_limit
    )
    if manifest != saved["manifest"] or counts != result["population"]:
        raise ValueError("Validation population or manifest differs")
    for path in paths.values():
        record(path)
    record(Path(config.validation_dataset) / "manifest.json")
    record(config.features)
    record(root / "catalog.parquet")
    users, items, history, popularity, bands = training_bands(paths["learning"])
    catalog = pq.read_table(root / "catalog.parquet")
    if (
        catalog["item_id"].to_pylist() != [r[0] for r in items]
        or catalog["users"].to_pylist() != [r[1] for r in items]
        or catalog["plays"].to_pylist() != [r[2] for r in items]
    ):
        raise ValueError("Saved catalog differs from learning data")
    references = {}
    for name, source in saved["sources"].items():
        directory = root / "candidates" / name
        record(directory / "result.json")
        record(directory / "config.json")
        candidate = read(directory / "result.json")
        frozen = read(directory / "config.json")
        signature = {
            k: saved[k] for k in ("settings", "manifest", "features_sha256", "source_sha256")
        }
        if (
            candidate["status"] != "succeeded"
            or candidate["phase"] != "validation"
            or candidate["signature"] != signature
            or candidate["source"] != source
            or candidate["population"] != counts
            or candidate["retrieval_m"] != 200
            or frozen != dict(signature=signature, source=source, retrieval_m=200)
        ):
            raise ValueError("Candidate provenance differs from frozen Hybrid study")
        verify_references([source["evidence"]])
        for entry in source["evidence"].values():
            record(entry["path"])
        verify_artifacts(directory, candidate["artifacts"])
        for entry in candidate["artifacts"].values():
            record(directory / entry["path"])
        references[name] = dict(directory=directory)
    partial.mkdir()
    schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("window_days", pa.int64()),
            ("stratum_kind", pa.string()),
            ("stratum", pa.string()),
            ("values", pa.list_(pa.float64())),
        ]
    )
    execution = dict(
        execution,
        dtype="float64",
        validation_memory_limit=validation_memory_limit,
        solver="set membership; no training",
        seed=None,
        seed_aggregation="equal per-user mean before paired differences",
    )
    atomic_json(
        partial / "config.json",
        dict(
            input_config=str(config.path),
            source_sha256=processing_hash,
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            evidence=evidence,
            strata=bands,
            execution=execution,
            phase="validation",
            selection="not_selected",
        ),
    )
    rows = []
    with (partial / "run.log").open("x") as log:
        for group, pairs in (
            ("ease-bpr", [("ease", f"bpr-s{seed}") for seed in (42, 43, 44)]),
            ("bm25-content", [("bm25", "content")]),
        ):
            seeds = []
            for left, right in pairs:
                writer = DetailWriter(partial / f"{left}-{right}.parquet", schema=schema)
                try:
                    seeds.append(
                        collect(
                            [references[left], references[right]],
                            paths["observations"],
                            users,
                            items,
                            history,
                            popularity,
                            config.evaluation.window_days,
                            xp=xp,
                            writer=writer,
                        )
                    )
                finally:
                    writer.close()
                log.write(f"Collected {left}/{right}: {len(users)} learning users\n")
                log.flush()
            rows.extend(summarize(seeds, history, group=group, xp=xp))
        write_csv(
            partial / "metrics.csv",
            rows,
            first=(
                "group",
                "task",
                "window_days",
                "stratum_kind",
                "stratum",
                "scope",
                "metric",
                "mean",
                "n_population",
                "n_evaluated",
                "n_excluded",
                "seeds",
            ),
        )
        save_figures(rows, partial)
        for path, digest in evidence.items():
            if file_sha256(path) != digest:
                raise ValueError("Input evidence changed during analysis")
        if source_hash() != processing_hash:
            raise ValueError("Processing source changed during analysis")
        log.write("SUCCEEDED\n")
    artifacts = {
        p.name: dict(path=p.name, sha256=file_sha256(p)) for p in partial.iterdir() if p.is_file()
    }
    atomic_json(
        partial / "result.json",
        dict(
            format="music-recsys-pool-strata-v1",
            status="succeeded",
            phase="validation",
            population=counts,
            selection="not_selected",
            strata=bands,
            execution=execution,
            elapsed_seconds=time.perf_counter() - started,
            artifacts=artifacts,
            reference_result=str(root / "result.json"),
            definitions=dict(
                scopes=["source_a100", "source_b100", "union100", "source_a200", "source_b200"],
                history="User bands from learning track counts; truth unchanged",
                popularity="Item bands from learning user counts; truth and hits restricted to band",
                denominator="Macro Recall over users with nonempty truth in the task/window/band",
                difference="Paired user mean of union minus source; seeds averaged per user first",
                uncertainty="Intervals not estimated; descriptive paired differences only",
                scope="Candidate diagnostic at fixed retrieval 100/200, not ranked Recall@K; "
                "four-aspect K2-20 evaluation and play/day distributions remain in reference_result",
                efficiency="Diagnostic elapsed time only; model latency/API cost not remeasured",
            ),
        ),
    )
    # The exclusive partial directory serializes writers; completed output is never reused.
    if output.exists():
        raise FileExistsError(output)
    os.rename(partial, output)
    return output, rows
