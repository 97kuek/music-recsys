"""保存済み履歴と分割の意味的整合を、メモリ上限付きの走査で検証する。"""

import tempfile
from datetime import datetime
from pathlib import Path

from src.ingestion.event_validation import assert_same_events
from src.ingestion.types import ValidationError

PARTITIONS = ("train", "validation", "test")
ROLES = ("events", *PARTITIONS)
TIME_INTERPRETATION = "source_naive_timezone_unverified"


def _boundaries(config: dict) -> tuple[datetime, datetime] | None:
    if not (
        "train_end" in config
        or "validation_end" in config
        or str(config.get("protocol", "")).startswith("temporal-")
    ):
        return None
    try:
        left = datetime.fromisoformat(config["train_end"])
        right = datetime.fromisoformat(config["validation_end"])
    except (KeyError, TypeError, ValueError):
        raise ValidationError("invalid temporal split boundaries") from None
    if (
        left.tzinfo is not None
        or right.tzinfo is not None
        or left >= right
        or config.get("timestamp_interpretation", TIME_INTERPRETATION) != TIME_INTERPRETATION
    ):
        raise ValidationError("invalid source-naive temporal split boundaries")
    return left, right


def validate_summary(config: dict, summary: dict, row_counts: dict[str, int]) -> None:
    """load時にも使う安価な検証。走査が必要なdistinct集計は公開時に照合する。"""
    boundaries = _boundaries(config)
    if row_counts["events"] != sum(row_counts[role] for role in PARTITIONS):
        raise ValidationError("partition event count mismatch")
    if not {"source", "partitions", "temporal"} & summary.keys():
        return
    if (
        boundaries is None
        or not isinstance(summary.get("source"), dict)
        or not isinstance(summary.get("partitions"), dict)
        or set(summary["partitions"]) != set(PARTITIONS)
        or summary.get("temporal")
        != {
            "train_end": config["train_end"],
            "validation_end": config["validation_end"],
            "timestamp_interpretation": config.get("timestamp_interpretation", TIME_INTERPRETATION),
        }
    ):
        raise ValidationError("invalid temporal split summary")
    left, right = boundaries
    for role in ROLES:
        stats = summary["source"] if role == "events" else summary["partitions"][role]
        if (
            not isinstance(stats, dict)
            or not {"events", "users", "tracks", "min_time", "max_time"} <= stats.keys()
        ):
            raise ValidationError("invalid partition summary structure")
        if (
            any(
                type(stats[key]) is not int or stats[key] < 0
                for key in ("events", "users", "tracks")
            )
            or stats["events"] != row_counts[role]
            or not 0 <= stats["users"] <= stats["events"]
            or not 0 <= stats["tracks"] <= stats["events"]
        ):
            raise ValidationError("partition summary counts mismatch")
        if stats["events"] == 0:
            if stats["min_time"] is not None or stats["max_time"] is not None:
                raise ValidationError("empty partition summary has timestamps")
            continue
        try:
            minimum, maximum = (
                datetime.fromisoformat(stats[key]) for key in ("min_time", "max_time")
            )
        except (TypeError, ValueError):
            raise ValidationError("invalid partition summary timestamps") from None
        if (
            minimum.tzinfo is not None
            or maximum.tzinfo is not None
            or minimum > maximum
            or not stats["users"]
            or not stats["tracks"]
            or (role == "train" and maximum >= left)
            or (role == "validation" and (minimum < left or maximum >= right))
            or (role == "test" and minimum < right)
        ):
            raise ValidationError("partition summary violates temporal boundaries")


def validate_saved_partitions(files: dict[str, Path], config: dict, summary: dict) -> None:
    """元履歴と3分割をmultisetで照合し、型・ID・時刻・実数集計を検証する。"""
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    counts = {}
    for role in ROLES:
        metadata = pq.ParquetFile(files[role])
        schema = metadata.schema_arrow
        if (
            schema.names != ["user_id", "item_id", "event_time"]
            or schema.field("user_id").type != pa.string()
            or schema.field("item_id").type != pa.string()
            or not pa.types.is_timestamp(schema.field("event_time").type)
            or schema.field("event_time").type.tz is not None
        ):
            raise ValidationError("expected source-naive event schema in every partition")
        counts[role] = metadata.metadata.num_rows
    validate_summary(config, summary, counts)
    boundaries = _boundaries(config)
    # 元ファイルと同じ保存領域に置き、全量のgroup-byがspillしてもRAMに載せない。
    with tempfile.TemporaryDirectory(
        prefix="split-verify-", dir=Path(files["events"]).parent
    ) as directory:
        with duckdb.connect(
            config={
                "memory_limit": "2GB",
                "threads": 2,
                "temp_directory": directory,
                "preserve_insertion_order": False,
            }
        ) as c:
            for role in ROLES:
                # テーブル名は内部定数。Parquetはviewで走査し、全件をPythonへ取り出さない。
                c.read_parquet(str(files[role])).create_view(role)
                invalid = c.execute(f"""SELECT 1 FROM {role}
                    WHERE user_id IS NULL OR regexp_matches(user_id, '^\\s*$')
                       OR strpos(user_id, chr(0)) > 0
                       OR item_id IS NULL OR regexp_matches(item_id, '^\\s*$')
                       OR strpos(item_id, chr(0)) > 0
                       OR event_time IS NULL OR NOT isfinite(event_time) LIMIT 1""").fetchone()
                if invalid:
                    raise ValidationError("split contains invalid IDs or timestamps")
                if "partitions" in summary:
                    row = c.execute(f"""SELECT count(*), count(DISTINCT user_id),
                        count(DISTINCT item_id), min(event_time), max(event_time) FROM {role}""").fetchone()
                    actual = dict(
                        zip(
                            ("events", "users", "tracks", "min_time", "max_time"),
                            (v.isoformat() if isinstance(v, datetime) else v for v in row),
                            strict=True,
                        )
                    )
                    expected = (
                        summary["source"] if role == "events" else summary["partitions"][role]
                    )
                    if any(expected[key] != value for key, value in actual.items()):
                        raise ValidationError("partition summary does not match actual events")
            if boundaries:
                left, right = boundaries
                for role, condition, params in (
                    ("train", "event_time >= ?", [left]),
                    ("validation", "event_time < ? OR event_time >= ?", [left, right]),
                    ("test", "event_time < ?", [right]),
                ):
                    if c.execute(
                        f"SELECT 1 FROM {role} WHERE {condition} LIMIT 1", params
                    ).fetchone():
                        raise ValidationError("partition events violate temporal boundaries")
            assert_same_events(
                c,
                "SELECT * FROM events",
                "SELECT * FROM train UNION ALL SELECT * FROM validation UNION ALL SELECT * FROM test",
                Path(directory),
            )
