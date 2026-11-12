"""Prepare all-user selector contexts without choosing a model or calling an API."""

import os

from backend.domain.recommendation.features import unit_vector
from backend.domain.recommendation.selection_context import SelectionContext
from backend.infrastructure.datasets.features import load_features
from backend.infrastructure.datasets.full_io import DetailWriter
from backend.infrastructure.datasets.listening_data import file_sha256, read_bundle
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.hybrid_config import read, reference
from backend.infrastructure.research.results import read_result
from backend.infrastructure.research.selection_candidates import bind_candidates
from backend.infrastructure.research.store import atomic_json


def prepare(config):
    import duckdb
    import pyarrow as pa

    original = read_result(config)
    if original["status"] != "succeeded" or original["phase"] != "validation":
        raise ValueError("Successful unchanged Hybrid Validation required")
    output, partial = (
        config.output / "selection-context",
        config.output / "selection-context.partial",
    )
    if output.exists() or partial.exists():
        raise FileExistsError("Existing selector contexts must be preserved")
    manifest, paths = read_bundle(config.validation_dataset)
    features = load_features(
        config.features,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    evidence = reference(config.output)
    for name, path in (("learning", paths["learning"]), ("features", config.features)):
        evidence[name] = dict(path=str(path), sha256=file_sha256(path))
    saved = read(config.output / "config.json")
    versions, candidate_evidence = bind_candidates(config.output, saved, original)
    evidence.update(candidate_evidence)
    processing = source_hash()
    partial.mkdir()
    atomic_json(
        partial / "config.json",
        dict(
            phase="validation",
            source_sha256=processing,
            evidence=evidence,
            candidates=versions,
            selection="not_selected",
            objective=None,
            model=None,
            budget_usd=None,
            external_transmission=False,
            definitions={
                "item_active_days": "Sum of per-track learning active days; not distinct calendar days",
                "feature_fraction": "Fraction of learning tracks with nonzero available static features",
                "boundary": "Learning aggregates only; no observations or evaluation values in selector payload",
            },
        ),
    )
    schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("history_count", pa.int64()),
            ("candidate_count", pa.int64()),
            ("total_plays", pa.int64()),
            ("item_active_days", pa.int64()),
            ("top_track_play_share", pa.float64()),
            ("feature_fraction", pa.float64()),
        ]
    )
    count = 0
    with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as connection:
        connection.read_parquet(str(paths["learning"])).create_view("learning")
        available = pa.table(
            {
                "item_id": pa.array(
                    [i for i, f in features.items() if unit_vector(f)], type=pa.string()
                )
            }
        )
        connection.register("available", available)
        catalog_size = connection.execute(
            "SELECT count(DISTINCT item_id) FROM learning"
        ).fetchone()[0]
        reader = connection.execute(
            """SELECT l.user_id,count(*) history_count,sum(play_count) total_plays,
            sum(active_days) item_active_days,max(play_count)::DOUBLE/sum(play_count) top_share,
            count(a.item_id)::DOUBLE/count(*) feature_fraction FROM learning l
            LEFT JOIN available a USING(item_id) GROUP BY l.user_id ORDER BY l.user_id"""
        ).to_arrow_reader(batch_size=4096)
        writer = DetailWriter(partial / "contexts.parquet", schema=schema)
        try:
            for batch in reader:
                for row in batch.to_pylist():
                    for task in ("new", "repeat"):
                        context = SelectionContext(
                            task,
                            row["history_count"],
                            row["history_count"]
                            if task == "repeat"
                            else catalog_size - row["history_count"],
                            int(row["total_plays"]),
                            int(row["item_active_days"]),
                            row["top_share"],
                            row["feature_fraction"],
                        )
                        writer.add(dict(user_id=row["user_id"], **context.payload()))
                    count += 1
        finally:
            writer.close()
    if count != original["population"]["users"]:
        raise ValueError("Selector population differs from the full learning population")
    if source_hash() != processing or any(
        file_sha256(r["path"]) != r["sha256"] for r in evidence.values()
    ):
        raise ValueError("Selector context evidence changed during preparation")
    (partial / "run.log").write_text(
        f"SUCCEEDED: {count} users, {2 * count} contexts; no API call\n"
    )
    atomic_json(
        partial / "result.json",
        dict(
            status="succeeded",
            phase="validation",
            purpose="selector_input_only",
            users=count,
            requests=2 * count,
            selection="not_selected",
            llm_evaluation="not_run",
            artifacts={p.name: dict(path=p.name, sha256=file_sha256(p)) for p in partial.iterdir()},
        ),
    )
    os.rename(partial, output)
    return output
