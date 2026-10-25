"""保存済み時系列分割から、メモリを制限して観測事実のv2 bundleを生成する。"""

import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from src.domain.datasets import canonical_json
from src.infrastructure.datasets.listening_data import file_sha256, parquet_schemas, validate_bundle
from src.infrastructure.provenance import source_hash
from src.paths import PROJECT_ROOT


def _copy(connection, query, path, parameters=None):
    connection.execute(
        f"COPY ({query}) TO $output (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)",
        {**(parameters or {}), "output": str(path)},
    )


def _assert_rows(connection, view, target, keys, work):
    """期待値を外部ソートし、小さなArrow batchで読戻しと厳密に照合する。"""
    import pyarrow.parquet as pq

    expected = work / "expected.parquet"
    _copy(connection, f"SELECT * FROM {view} ORDER BY {','.join(keys)}", expected)
    left = iter(pq.ParquetFile(expected).iter_batches(batch_size=65536))
    right = iter(pq.ParquetFile(target).iter_batches(batch_size=65536))
    a, b = next(left, None), next(right, None)
    while a is not None and b is not None:
        count = min(a.num_rows, b.num_rows)
        if not a.slice(0, count).equals(b.slice(0, count), check_metadata=False):
            raise ValueError("Parquet readback differs from aggregated observations")
        a = a.slice(count) if a.num_rows > count else next(left, None)
        b = b.slice(count) if b.num_rows > count else next(right, None)
    if a is not None or b is not None:
        raise ValueError("Parquet readback row count mismatch")


def export_listening_phase(
    files, output, temporal, *, phase, windows=(30, 90, 180), provenance, memory_limit="2GB"
):
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    if phase not in {"validation", "test"}:
        raise ValueError("phase must be validation or test")
    if (
        not windows
        or any(type(w) is not int or w <= 0 for w in windows)
        or len(set(windows)) != len(windows)
    ):
        raise ValueError("windows must be positive unique day counts")
    windows = tuple(sorted(windows))
    prediction = datetime.fromisoformat(
        temporal.train_end if phase == "validation" else temporal.validation_end
    )
    if phase == "validation" and prediction + timedelta(days=max(windows)) > datetime.fromisoformat(
        temporal.validation_end
    ):
        raise ValueError("Validation window crosses Test boundary")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # 新規のみ。失敗途中のbundleにmanifestを付けない。
    roles = ("train", "validation") if phase == "validation" else ("train", "validation", "test")
    schemas, source_files = parquet_schemas(), {}
    with tempfile.TemporaryDirectory(prefix=".aggregate-", dir=output) as directory:
        work = Path(directory)
        with duckdb.connect(
            config={
                "memory_limit": memory_limit,
                "threads": 2,
                "temp_directory": str(work / "spill"),
            }
        ) as connection:
            for role in roles:
                path = Path(files[role])
                schema = pq.ParquetFile(path).schema_arrow
                if (
                    schema.names != ["user_id", "item_id", "event_time"]
                    or not pa.types.is_string(schema.field("user_id").type)
                    or not pa.types.is_string(schema.field("item_id").type)
                    or not pa.types.is_timestamp(schema.field("event_time").type)
                    or schema.field("event_time").type.tz is not None
                ):
                    raise ValueError("source requires string IDs and source-naive timestamps")
                source_files[role] = {
                    "format": "parquet",
                    "sha256": file_sha256(path),
                    "byte_size": path.stat().st_size,
                    "row_count": pq.ParquetFile(path).metadata.num_rows,
                }
                expected = provenance.get("source_artifacts", {}).get(role)
                if expected and any(
                    source_files[role][key] != expected[key] for key in ("sha256", "row_count")
                ):
                    raise ValueError("source artifact checksum / row count mismatch")
                connection.read_parquet(str(path)).create_view(f"{role}_events")
                lower = temporal.train_end if role == "validation" else temporal.validation_end
                upper = temporal.train_end if role == "train" else temporal.validation_end
                invalid = (
                    "event_time >= $upper"
                    if role == "train"
                    else "event_time < $lower OR event_time >= $upper"
                    if role == "validation"
                    else "event_time < $lower"
                )
                parameters = (
                    {"upper": datetime.fromisoformat(upper)}
                    if role == "train"
                    else {
                        "lower": datetime.fromisoformat(lower),
                        "upper": datetime.fromisoformat(upper),
                    }
                    if role == "validation"
                    else {"lower": datetime.fromisoformat(lower)}
                )
                if connection.execute(
                    f"SELECT count(*) FROM {role}_events WHERE user_id IS NULL OR item_id IS NULL OR trim(user_id)='' OR trim(item_id)='' OR event_time IS NULL OR ({invalid})",
                    parameters,
                ).fetchone()[0]:
                    raise ValueError("source IDs / temporal partition boundaries are inconsistent")
            query = "SELECT * FROM train_events" + (
                " UNION ALL SELECT * FROM validation_events" if phase == "test" else ""
            )
            connection.execute(f"CREATE TEMP VIEW learning_events AS {query}")
            learning_events = connection.execute("SELECT count(*) FROM learning_events").fetchone()[
                0
            ]
            for label, query in (
                ("learning", "SELECT *,hash(user_id)%32 AS bucket FROM learning_events"),
                (
                    "evaluation",
                    f"SELECT *,hash(user_id)%32 AS bucket FROM {phase}_events WHERE event_time >= $start AND event_time < $end",
                ),
            ):
                target = work / f"{label}-buckets"
                params = {"target": str(target)}
                if label == "evaluation":
                    params.update(start=prediction, end=prediction + timedelta(days=max(windows)))
                connection.execute(
                    f"COPY ({query}) TO $target (FORMAT PARQUET, PARTITION_BY(bucket), COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)",
                    params,
                )
            learning_parts = []
            for bucket in range(32):
                sources = sorted((work / "learning-buckets" / f"bucket={bucket}").glob("*.parquet"))
                if sources:
                    connection.read_parquet(
                        [str(p) for p in sources], hive_partitioning=False
                    ).create_view("bucket_events", replace=True)
                else:
                    connection.execute(
                        "CREATE OR REPLACE VIEW bucket_events AS SELECT * FROM learning_events WHERE false"
                    )
                target = work / f"learning-{bucket}.parquet"
                _copy(
                    connection,
                    "SELECT user_id,item_id,count(*)::BIGINT play_count,count(DISTINCT CAST(event_time AS DATE))::BIGINT active_days FROM bucket_events GROUP BY user_id,item_id ORDER BY user_id,item_id",
                    target,
                )
                learning_parts.append(str(target))
            connection.read_parquet(learning_parts).create_view("all_learning")
            connection.execute(
                "CREATE TEMP TABLE known_items AS SELECT DISTINCT item_id FROM all_learning"
            )
            connection.execute("SET threads=1")  # 最終Parquetの行順・writer設定を固定する。
            learning_path = output / "learning.parquet"
            _copy(connection, "SELECT * FROM all_learning ORDER BY user_id,item_id", learning_path)
            _assert_rows(connection, "all_learning", learning_path, ("user_id", "item_id"), work)
            if (
                connection.execute(
                    "SELECT coalesce(sum(play_count),0) FROM all_learning"
                ).fetchone()[0]
                != learning_events
            ):
                raise ValueError("learning play count sum differs from source")
            exclusions = {str(w): {"cold_user_events": 0, "cold_item_events": 0} for w in windows}
            observation_parts = []
            for bucket in range(32):
                sources = sorted(
                    (work / "evaluation-buckets" / f"bucket={bucket}").glob("*.parquet")
                )
                if not sources:
                    continue
                connection.read_parquet(
                    [str(p) for p in sources], hive_partitioning=False
                ).create_view("bucket_evaluation", replace=True)
                connection.read_parquet(learning_parts[bucket]).create_view(
                    "bucket_learning", replace=True
                )
                for window in windows:
                    connection.execute(
                        """CREATE OR REPLACE TEMP TABLE daily AS SELECT user_id,item_id,CAST(event_time AS DATE) AS day,count(*)::BIGINT plays FROM bucket_evaluation WHERE event_time >= $start AND event_time < $end GROUP BY user_id,item_id,day""",
                        {"start": prediction, "end": prediction + timedelta(days=window)},
                    )
                    connection.execute("""CREATE OR REPLACE TEMP TABLE classified AS
                        SELECT e.user_id,e.item_id,e.plays,e.active_days,
                        coalesce(l.play_count,0)::BIGINT learning_play_count,
                        coalesce(l.active_days,0)::BIGINT learning_active_days,
                        CASE WHEN u.user_id IS NULL THEN 'cold_user' WHEN i.item_id IS NULL THEN 'cold_item' ELSE 'warm' END status
                        FROM (SELECT user_id,item_id,sum(plays)::BIGINT AS plays,count(*)::BIGINT AS active_days FROM daily GROUP BY user_id,item_id) e
                        LEFT JOIN bucket_learning l USING(user_id,item_id)
                        LEFT JOIN (SELECT DISTINCT user_id FROM bucket_learning) u USING(user_id)
                        LEFT JOIN known_items i USING(item_id)""")
                    for status in ("cold_user", "cold_item"):
                        count = connection.execute(
                            "SELECT coalesce(sum(plays),0) FROM classified WHERE status=?", [status]
                        ).fetchone()[0]
                        exclusions[str(window)][f"{status}_events"] += int(count)
                    target = work / f"observations-{bucket}-{window}.parquet"
                    _copy(
                        connection,
                        "SELECT user_id,item_id,$window::BIGINT AS window_days,learning_play_count,learning_active_days,plays AS evaluation_play_count,active_days AS evaluation_active_days FROM classified WHERE status='warm' ORDER BY user_id,item_id",
                        target,
                        {"window": window},
                    )
                    observation_parts.append(str(target))
            observations_path = output / "observations.parquet"
            if observation_parts:
                connection.read_parquet(observation_parts).create_view("all_observations")
                _copy(
                    connection,
                    "SELECT * FROM all_observations ORDER BY user_id,item_id,window_days",
                    observations_path,
                )
                _assert_rows(
                    connection,
                    "all_observations",
                    observations_path,
                    ("user_id", "item_id", "window_days"),
                    work,
                )
            else:
                pq.write_table(
                    pa.Table.from_pylist([], schema=schemas["observations"]),
                    observations_path,
                    compression="zstd",
                    row_group_size=65536,
                )
    refs = {
        role: {
            "path": f"{role}.parquet",
            "format": "parquet",
            "sha256": file_sha256(output / f"{role}.parquet"),
            "byte_size": (output / f"{role}.parquet").stat().st_size,
            "row_count": pq.ParquetFile(output / f"{role}.parquet").metadata.num_rows,
        }
        for role in ("learning", "observations")
    }
    lock = PROJECT_ROOT / "uv.lock"
    manifest = {
        "schema_version": 2,
        "protocol": "listening-v2",
        "dataset_id": provenance.get("dataset_key", "music4all-onion"),
        "phase": phase,
        "prediction_at": prediction.isoformat(),
        "temporal": asdict(temporal),
        "window_days": list(windows),
        "window_ends": {str(w): (prediction + timedelta(days=w)).isoformat() for w in windows},
        "source": provenance,
        "source_files": source_files,
        "files": refs,
        "exclusions": exclusions,
        "exclusion_order": ["cold_user", "cold_item"],
        "source_sha256": source_hash(),
        "dependency_lock_sha256": file_sha256(lock),
        "timestamp_interpretation": temporal.timestamp_interpretation,
    }
    # 最終manifestは検証成功後にだけ公開する。一時manifestも未完成bundleの外へ置く。
    with tempfile.TemporaryDirectory(prefix="validate-", dir=output.parent) as directory:
        verification = Path(directory)
        for role in refs:
            # 大きなファイルを複製せず、bundle内の参照として検証する。
            os.link(output / f"{role}.parquet", verification / f"{role}.parquet")
        (verification / "manifest.json").write_text(canonical_json(manifest) + "\n")
        validate_bundle(verification)
    target = output / "manifest.json"
    temporary = output / ".manifest.tmp"
    with temporary.open("w") as stream:
        stream.write(canonical_json(manifest) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return target
