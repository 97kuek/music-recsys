"""保存済み再生履歴の時系列分割と、Warm Start・未聴曲の評価入力を作る。"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from music_recsys.ingestion.event_validation import assert_same_events
from music_recsys.ingestion.types import canonical_json


@dataclass(frozen=True)
class TemporalConfig:
    train_end: str = "2017-03-29T00:00:00"
    validation_end: str = "2018-09-21T00:00:00"
    timestamp_interpretation: str = "source_naive_timezone_unverified"

    def __post_init__(self):
        left, right = (
            datetime.fromisoformat(self.train_end),
            datetime.fromisoformat(self.validation_end),
        )
        if left.tzinfo is not None or right.tzinfo is not None or left >= right:
            raise ValueError("boundaries must be increasing source-naive datetimes")
        if self.timestamp_interpretation != "source_naive_timezone_unverified":
            raise ValueError("source timezone is unverified; do not assign a timezone")


@dataclass(frozen=True)
class TemporalResult:
    files: dict[str, Path]
    summary: dict


def _statistics(connection, table: str) -> dict:
    row = connection.execute(f"""SELECT count(*), count(DISTINCT user_id),
        count(DISTINCT item_id), min(event_time), max(event_time) FROM {table}""").fetchone()
    return dict(
        zip(
            ("events", "users", "tracks", "min_time", "max_time"),
            (v.isoformat() if isinstance(v, datetime) else v for v in row),
            strict=True,
        )
    )


def _evaluation(connection, phase: str) -> dict:
    # phase/table names are internal constants, never user-supplied SQL.
    train = "train" if phase == "validation" else "learning_test"
    connection.execute(f"CREATE OR REPLACE TEMP VIEW learning AS SELECT * FROM {train}")
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE histories AS SELECT DISTINCT user_id,item_id FROM learning"
    )
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE known_users AS SELECT DISTINCT user_id FROM histories"
    )
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE known_items AS SELECT DISTINCT item_id FROM histories"
    )
    connection.execute(f"""CREATE OR REPLACE TEMP TABLE classified AS
        SELECT e.user_id,e.item_id,count(*) AS plays,
          CASE WHEN u.user_id IS NULL THEN 'cold_user'
               WHEN i.item_id IS NULL THEN 'cold_item'
               WHEN h.user_id IS NOT NULL THEN 'seen' ELSE 'eligible' END AS status
        FROM {phase} e LEFT JOIN known_users u USING(user_id)
        LEFT JOIN known_items i USING(item_id)
        LEFT JOIN histories h USING(user_id,item_id)
        GROUP BY e.user_id,e.item_id,status""")
    stats = {
        "training": _statistics(connection, "learning"),
        "exclusion_order": ["cold_user", "cold_item", "seen"],
    }
    for status in ("cold_user", "cold_item", "seen", "eligible"):
        row = connection.execute(
            "SELECT coalesce(sum(plays),0),count(*),count(DISTINCT user_id),count(DISTINCT item_id) FROM classified WHERE status=?",
            [status],
        ).fetchone()
        stats.update(
            {
                f"{status}_{name}": int(value)
                for name, value in zip(("events", "pairs", "users", "tracks"), row, strict=True)
            }
        )
    stats["requests"] = connection.execute("SELECT count(*) FROM known_users").fetchone()[0]
    stats["evaluable_users"] = stats["eligible_users"]
    stats["users_without_ground_truth"] = stats["requests"] - stats["evaluable_users"]
    stats["ground_truth_pairs"] = stats["eligible_pairs"]
    # Independent anti-join assertion: eligible truth must be known and never previously played.
    violations = connection.execute("""SELECT count(*) FROM classified e
        LEFT JOIN known_users u USING(user_id) LEFT JOIN known_items i USING(item_id)
        LEFT JOIN histories h USING(user_id,item_id)
        WHERE e.status='eligible' AND (u.user_id IS NULL OR i.item_id IS NULL OR h.user_id IS NOT NULL)""").fetchone()[
        0
    ]
    if violations:
        raise ValueError("warm-start/novelty leakage detected")
    return stats


def _export_benchmark(connection, path: Path, phase: str, provenance: dict, config: TemporalConfig):
    # JSON is deliberately limited to smoke experiments. Full events stay in Parquet.
    users = [
        r[0]
        for r in connection.execute("SELECT user_id FROM known_users ORDER BY user_id").fetchall()
    ]
    n = connection.execute("SELECT count(*) FROM learning").fetchone()[0]
    if len(users) > 2000 or n > 5_000_000:
        raise ValueError(
            "explicit JSON benchmark is limited to 2000 users / 5 million learning events"
        )
    history = dict(
        connection.execute(
            "SELECT user_id,list(item_id ORDER BY item_id) FROM histories GROUP BY user_id"
        ).fetchall()
    )
    truth = dict(
        connection.execute(
            "SELECT user_id,list(item_id ORDER BY item_id) FROM classified WHERE status='eligible' GROUP BY user_id"
        ).fetchall()
    )
    catalog = [
        r[0]
        for r in connection.execute("SELECT item_id FROM known_items ORDER BY item_id").fetchall()
    ]
    if not users or not catalog:
        raise ValueError("training data is empty")
    requests = [
        {"request_id": f"{phase}-{index:06d}", "user_id": user, "history": history[user]}
        for index, user in enumerate(users)
    ]
    raw = {
        "schema_version": 1,
        "dataset_id": "music4all-onion",
        "provenance": {
            **provenance,
            "phase": phase,
            "temporal": asdict(config),
            "training_periods": ["train"] if phase == "validation" else ["train", "validation"],
            "candidate_policy": "learning_items_minus_all_learning_history",
            "ground_truth": "unique_new_warm_items_in_evaluation_period",
            "purpose": "small_scale_smoke_validation",
        },
        "catalog_ids": catalog,
        "train_interactions": [
            {"user_id": u, "item_id": i}
            for u, i in connection.execute(
                "SELECT user_id,item_id FROM learning ORDER BY user_id,item_id,event_time"
            ).fetchall()
        ],
        "requests": requests,
        "ground_truth": [
            {"request_id": r["request_id"], "relevant_item_ids": truth.get(r["user_id"], [])}
            for r in requests
        ],
    }
    path.write_text(canonical_json(raw) + "\n", encoding="utf-8")


def prepare_temporal(
    events: Path,
    output: Path,
    config: TemporalConfig,
    *,
    provenance: dict,
    export_benchmarks: bool = False,
    progress: Callable[[str], None] | None = None,
) -> TemporalResult:
    """新規ディレクトリに分割を作り、読戻し検証する。DB公開は呼出側で行う。"""
    import duckdb
    import pyarrow.parquet as pq

    report = progress or (lambda message: None)
    schema = pq.read_schema(events)
    import pyarrow as pa

    if (
        schema.names != ["user_id", "item_id", "event_time"]
        or schema.field("user_id").type != pa.string()
        or schema.field("item_id").type != pa.string()
        or not pa.types.is_timestamp(schema.field("event_time").type)
        or schema.field("event_time").type.tz is not None
    ):
        raise ValueError("expected source-naive event schema")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    files = {"events": Path(events)}
    with duckdb.connect(
        config={
            "memory_limit": "2GB",
            "threads": 2,
            "temp_directory": str(output / "spill"),
            "preserve_insertion_order": False,
        }
    ) as c:
        report("Reading source events")
        c.read_parquet(str(events)).create_view("source")
        if c.execute(
            "SELECT count(*) FROM source WHERE user_id IS NULL OR trim(user_id)='' OR item_id IS NULL OR trim(item_id)='' OR event_time IS NULL"
        ).fetchone()[0]:
            raise ValueError("events contain invalid IDs or missing timestamps")
        left, right = (
            datetime.fromisoformat(config.train_end),
            datetime.fromisoformat(config.validation_end),
        )
        c.sql("SELECT * FROM source WHERE event_time < ?", params=[left]).create_view("train")
        c.sql(
            "SELECT * FROM source WHERE event_time >= ? AND event_time < ?",
            params=[left, right],
        ).create_view("validation")
        c.sql("SELECT * FROM source WHERE event_time >= ?", params=[right]).create_view("test")
        report("Summarizing source events")
        summary = {
            "temporal": asdict(config),
            "source": _statistics(c, "source"),
            "partitions": {},
            "evaluation": {},
        }
        for phase in ("train", "validation", "test"):
            report(f"Writing {phase} partition")
            path = output / f"{phase}.parquet"
            # Single writer plus explicit order makes repeated output independent of scan order.
            c.execute("SET threads=1")
            c.execute(
                f"COPY (SELECT * FROM {phase} ORDER BY user_id,item_id,event_time) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)",
                [str(path)],
            )
            c.execute("SET threads=2")
            files[phase] = path
            summary["partitions"][phase] = _statistics(c, phase)
            report(f"Verifying {phase} round-trip")
            c.read_parquet(str(path)).create_view("written_partition", replace=True)
            assert_same_events(
                c, f"SELECT * FROM {phase}", "SELECT * FROM written_partition", output
            )
        if sum(s["events"] for s in summary["partitions"].values()) != summary["source"]["events"]:
            raise ValueError("partition event count mismatch")
        c.execute(
            "CREATE TEMP VIEW learning_test AS SELECT * FROM train UNION ALL SELECT * FROM validation"
        )
        for phase in ("validation", "test"):
            report(f"Validating {phase} warm-start ground truth")
            summary["evaluation"][phase] = _evaluation(c, phase)
            if export_benchmarks:
                files[f"{phase}_benchmark"] = output / f"{phase}.json"
                _export_benchmark(c, files[f"{phase}_benchmark"], phase, provenance, config)
        summary["leakage_checks"] = {
            "passed": True,
            "partition_roundtrip_multiset": True,
            "periods_disjoint_and_exhaustive": True,
            "ground_truth_warm_and_unseen": True,
            "test_training_is_train_plus_validation": True,
            "source_timezone": "unverified; no UTC conversion",
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return TemporalResult(files, summary)


def export_phase(
    files: dict[str, Path], output: Path, config: TemporalConfig, *, phase: str, provenance: dict
) -> None:
    """検証・公開済み分割から小規模Runner用データを生成する。再分割は行わない。"""
    import duckdb

    if phase not in {"validation", "test"}:
        raise ValueError("evaluation phase must be validation or test")
    if Path(output).exists():
        raise FileExistsError("benchmark output already exists")
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as c:
        for name in ("train", "validation", "test"):
            c.execute(
                f"CREATE TEMP TABLE {name} AS SELECT * FROM read_parquet(?)", [str(files[name])]
            )
        c.execute(
            "CREATE TEMP VIEW learning_test AS SELECT * FROM train UNION ALL SELECT * FROM validation"
        )
        _evaluation(c, phase)
        _export_benchmark(c, Path(output), phase, provenance, config)
