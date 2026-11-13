"""Validate train-only provenance and event counts without materializing scores."""

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from backend.infrastructure.datasets.listening_data import file_sha256


@contextmanager
def validated_event_history(
    learning_path,
    events_path,
    expected_sha256,
    prediction_at,
    memory_limit,
    *,
    extra_columns="",
    extra_parameters=(),
):
    """Yield validated SQL history; optional aggregate expressions are internal SQL."""
    import duckdb

    cutoff = datetime.fromisoformat(prediction_at)
    if cutoff.tzinfo is not None:
        raise ValueError("Source-naive prediction timestamp required")
    paths = [events_path] if isinstance(events_path, (str, Path)) else list(events_path)
    hashes = [expected_sha256] if isinstance(expected_sha256, str) else list(expected_sha256)
    if not paths or len(paths) != len(hashes) or len(set(map(str, paths))) != len(paths):
        raise ValueError("Distinct event sources and matching hashes required")
    if any(file_sha256(p) != h for p, h in zip(paths, hashes, strict=True)):
        raise ValueError("Training events hash mismatch")
    with duckdb.connect(config={"threads": 2, "memory_limit": memory_limit}) as c:
        invalid = c.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE event_time IS NULL "
            "OR event_time >= ? OR user_id IS NULL OR item_id IS NULL",
            [[str(p) for p in paths], cutoff],
        ).fetchone()[0]
        if invalid:
            raise ValueError("Training events contain missing or future observations")
        c.execute(
            "CREATE TEMP TABLE history AS SELECT user_id,item_id,count(*) AS n,"
            f"max(event_time) AS last_play{extra_columns} "
            "FROM read_parquet(?) GROUP BY user_id,item_id",
            [*extra_parameters, [str(p) for p in paths]],
        )
        mismatches = c.execute(
            "SELECT count(*) FROM history h FULL OUTER JOIN read_parquet(?) l "
            "USING(user_id,item_id) WHERE h.n IS DISTINCT FROM l.play_count",
            [str(learning_path)],
        ).fetchone()[0]
        if mismatches:
            raise ValueError("Training events do not reproduce learning counts")
        yield c
    if any(file_sha256(p) != h for p, h in zip(paths, hashes, strict=True)):
        raise ValueError("Training events changed during aggregation")
