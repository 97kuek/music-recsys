"""Music4All-Onionのストリーム変換と全体時系列の探索的集計。

DuckDB / PyArrowはdata依存グループで導入する。分割境界は候補であり、
この処理はベンチマーク用データの確定・モデル学習を行わない。
"""

import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _connect(parent: Path):
    import duckdb

    return duckdb.connect(
        config={
            "memory_limit": "2GB",
            "threads": 2,
            "preserve_insertion_order": False,
            "temp_directory": str(parent / "duckdb_tmp"),
        }
    )


def convert_events(
    source: Path,
    destination: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """bzip2をバッチで読み、検証後にParquetを公開する。既存cacheは照合する。"""
    import pyarrow as pa
    import pyarrow.csv as csv
    import pyarrow.parquet as pq

    source, destination = Path(source), Path(destination)
    with source.open("rb") as stream:
        source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest_path = destination.with_suffix(".manifest.json")
    if destination.exists():
        if not manifest_path.exists():
            raise ValueError("existing cache has no source manifest")
        manifest = json.loads(manifest_path.read_text())
        if manifest["source_sha256"] != source_sha256:
            raise ValueError("existing cache was created from a different source")
        with destination.open("rb") as cached:
            if hashlib.file_digest(cached, "sha256").hexdigest() != manifest.get("parquet_sha256"):
                raise ValueError("cache content does not match source manifest")
        if pq.ParquetFile(destination).metadata.num_rows != manifest["n_events"]:
            raise ValueError("cache row count does not match source manifest")
        return manifest
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(suffix=".parquet", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with pa.OSFile(str(source), "rb") as raw, pa.CompressedInputStream(raw, "bz2") as stream:
            reader = csv.open_csv(
                stream,
                read_options=csv.ReadOptions(block_size=16 * 1024 * 1024),
                parse_options=csv.ParseOptions(delimiter="\t"),
                convert_options=csv.ConvertOptions(
                    column_types={
                        "user_id": pa.string(),
                        "track_id": pa.string(),
                        "timestamp": pa.timestamp("s"),
                    },
                    strings_can_be_null=True,
                ),
            )
            if reader.schema.names != ["user_id", "track_id", "timestamp"]:
                raise ValueError("unexpected event columns")

            def batches():
                count = 0
                last_reported = 0
                for batch in reader:
                    count += batch.num_rows
                    if progress and count - last_reported >= 5_000_000:
                        progress(f"Converted {count:,} events")
                        last_reported = count
                    yield batch

            with _connect(destination.parent) as connection:
                batched = pa.RecordBatchReader.from_batches(reader.schema, batches())
                connection.register("event_stream", batched)
                connection.execute(
                    'COPY (SELECT user_id, track_id AS item_id, "timestamp" AS event_time '
                    "FROM event_stream) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 262144)",
                    [str(temporary)],
                )
                invalid = connection.execute(
                    "SELECT count(*) FROM read_parquet(?) WHERE user_id IS NULL OR user_id = '' "
                    "OR item_id IS NULL OR item_id = '' OR event_time IS NULL",
                    [str(temporary)],
                ).fetchone()[0]
                if invalid:
                    raise ValueError("event data contains missing IDs or timestamps")
        with temporary.open("rb") as converted:
            parquet_sha256 = hashlib.file_digest(converted, "sha256").hexdigest()
        manifest = {
            "format_version": 1,
            "pyarrow_version": pa.__version__,
            "source_sha256": source_sha256,
            "parquet_sha256": parquet_sha256,
            "source_bytes": source.stat().st_size,
            "n_events": pq.ParquetFile(temporary).metadata.num_rows,
            "timestamp_interpretation": "timezone-naive values as provided; timezone not verified",
        }
        os.link(temporary, destination)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest
    except pa.ArrowInvalid as error:
        raise ValueError("invalid timestamp or event data") from error
    finally:
        temporary.unlink(missing_ok=True)


def _propose_boundaries(daily: list[dict]) -> tuple[datetime, datetime]:
    """イベント数の80%/90%に最も近い日境界。日内のイベントを分けない。"""
    total = sum(row["n_events"] for row in daily)
    if total == 0:
        raise ValueError("cannot propose boundaries for empty events")
    options = []
    cumulative = 0
    for row in daily:
        cumulative += row["n_events"]
        boundary = datetime.fromisoformat(row["day"]) + timedelta(days=1)
        options.append((cumulative, boundary))
    result = [
        min(options, key=lambda row: (abs(row[0] / total - ratio), row[1]))[1]
        for ratio in (0.8, 0.9)
    ]
    if result[0] >= result[1]:
        raise ValueError("cannot propose distinct daily boundaries; provide explicit boundaries")
    return result[0], result[1]


def _phase(
    connection, *, label: str, train_end: datetime, prediction_time: datetime, count_column: str
) -> dict:
    # 列名はこのモジュール内の定数だけを使用し、時刻はSQLパラメータで渡す。
    connection.execute(
        f"CREATE OR REPLACE TEMP TABLE classified AS SELECT p.user_id, p.{count_column} AS n_events, "
        "CASE WHEN u.first_at >= ? THEN 'cold_user' WHEN i.first_at >= ? THEN 'cold_item' "
        "WHEN p.first_at < ? THEN 'seen' ELSE 'eligible' END AS reason "
        "FROM pairs p JOIN user_first u USING(user_id) JOIN item_first i USING(item_id) "
        f"WHERE p.{count_column} > 0",
        [train_end, train_end, prediction_time],
    )
    counts = dict(
        connection.execute("SELECT reason, count(*) FROM classified GROUP BY reason").fetchall()
    )
    novel_users, novel_pairs = connection.execute(
        "SELECT count(DISTINCT user_id), count(*) FROM classified WHERE reason = 'eligible'"
    ).fetchone()
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE historical_counts AS SELECT user_id, count(*) AS n_tracks "
        "FROM pairs WHERE first_at < ? GROUP BY user_id",
        [train_end],
    )
    thresholds = {}
    for minimum in (1, 5, 10, 20):
        thresholds[str(minimum)] = connection.execute(
            "SELECT count(DISTINCT c.user_id) FROM classified c JOIN historical_counts h USING(user_id) "
            "WHERE c.reason = 'eligible' AND h.n_tracks >= ?",
            [minimum],
        ).fetchone()[0]
    return {
        "label": label,
        "learning_cutoff": train_end.isoformat(),
        "prediction_time": prediction_time.isoformat(),
        "novel_users": novel_users,
        "novel_pairs": novel_pairs,
        "pair_reasons": {
            key: counts.get(key, 0) for key in ("eligible", "cold_user", "cold_item", "seen")
        },
        "users_with_truth_by_min_history": thresholds,
    }


def profile_events(
    parquet_path: Path,
    *,
    train_end: str | None = None,
    validation_end: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """全量の時刻分布を取り、日境界の初期案と2種類の最終学習条件を集計する。"""
    path = Path(parquet_path)
    manifest_path = path.with_suffix(".manifest.json")
    provenance = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if provenance is not None:
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != provenance["parquet_sha256"]:
                raise ValueError("cache content does not match source manifest")
    with _connect(path.parent) as connection:
        connection.read_parquet(str(path)).create_view("events")
        n_events, n_users, n_items, start, end = connection.execute(
            "SELECT count(*), count(DISTINCT user_id), count(DISTINCT item_id), "
            "min(event_time), max(event_time) FROM events"
        ).fetchone()
        if not n_events:
            raise ValueError("event data is empty")
        daily = [
            {"day": day.isoformat(), "n_events": count}
            for day, count in connection.execute(
                "SELECT CAST(event_time AS DATE) AS day, count(*) FROM events GROUP BY day ORDER BY day"
            ).fetchall()
        ]
        if train_end is None and validation_end is None:
            t1, t2 = _propose_boundaries(daily)
        elif train_end is None or validation_end is None:
            raise ValueError("both boundaries must be supplied")
        else:
            t1, t2 = datetime.fromisoformat(train_end), datetime.fromisoformat(validation_end)
        if t1.tzinfo is not None or t2.tzinfo is not None or not start < t1 < t2 <= end:
            raise ValueError(
                "boundaries must satisfy min_time < train_end < validation_end <= max_time"
            )
        split = connection.execute(
            "SELECT count(*) FILTER(WHERE event_time < ?), "
            "count(*) FILTER(WHERE event_time >= ? AND event_time < ?), "
            "count(*) FILTER(WHERE event_time >= ?) FROM events",
            [t1, t1, t2, t2],
        ).fetchone()
        if progress:
            progress(f"Daily distribution complete: {n_events:,} events; boundaries {t1} / {t2}")
            progress("Aggregating user/item pairs with bounded memory and disk spill")
        connection.execute(
            "CREATE TEMP TABLE pairs AS SELECT user_id, item_id, min(event_time) AS first_at, "
            "count(*) FILTER(WHERE event_time < ?) AS train_count, "
            "count(*) FILTER(WHERE event_time >= ? AND event_time < ?) AS validation_count, "
            "count(*) FILTER(WHERE event_time >= ?) AS test_count "
            "FROM events GROUP BY user_id, item_id",
            [t1, t1, t2, t2],
        )
        connection.execute(
            "CREATE TEMP TABLE user_first AS SELECT user_id, min(first_at) AS first_at FROM pairs GROUP BY user_id"
        )
        connection.execute(
            "CREATE TEMP TABLE item_first AS SELECT item_id, min(first_at) AS first_at FROM pairs GROUP BY item_id"
        )
        connection.execute(
            "CREATE TEMP TABLE train_history AS SELECT user_id, count(*) AS n_tracks, "
            "sum(train_count) AS n_events FROM pairs WHERE train_count > 0 GROUP BY user_id"
        )
        probabilities = [0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1.0]
        unique_quantiles, event_quantiles = connection.execute(
            "SELECT quantile_cont(n_tracks, ?), quantile_cont(n_events, ?) FROM train_history",
            [probabilities, probabilities],
        ).fetchone()
        train_users = connection.execute("SELECT count(*) FROM train_history").fetchone()[0]
        train_items = connection.execute(
            "SELECT count(*) FROM item_first WHERE first_at < ?", [t1]
        ).fetchone()[0]
        train = {
            "n_users": train_users,
            "n_items": train_items,
            "unique_tracks_quantiles": dict(
                zip(map(str, probabilities), unique_quantiles, strict=True)
            ),
            "event_count_quantiles": dict(
                zip(map(str, probabilities), event_quantiles, strict=True)
            ),
        }
        validation = _phase(
            connection,
            label="validation",
            train_end=t1,
            prediction_time=t1,
            count_column="validation_count",
        )
        test_fixed = _phase(
            connection,
            label="test_train_only",
            train_end=t1,
            prediction_time=t2,
            count_column="test_count",
        )
        test_refit = _phase(
            connection,
            label="test_refit_train_validation",
            train_end=t2,
            prediction_time=t2,
            count_column="test_count",
        )
        return {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "duckdb_version": connection.execute("SELECT version()").fetchone()[0],
            "provenance": provenance,
            "n_events": n_events,
            "n_users": n_users,
            "n_items": n_items,
            "min_time": start.isoformat(),
            "max_time": end.isoformat(),
            "boundaries": {"train_end": t1.isoformat(), "validation_end": t2.isoformat()},
            "split_events": dict(zip(("train", "validation", "test"), split, strict=True)),
            "split_fractions": dict(
                zip(
                    ("train", "validation", "test"),
                    (count / n_events for count in split),
                    strict=True,
                )
            ),
            "daily_counts": daily,
            "train": train,
            "validation": validation,
            "test_train_only": test_fixed,
            "test_refit_train_validation": test_refit,
            "reason_priority": "cold_user, cold_item, seen, eligible; mutually exclusive pair counts",
            "timestamp_interpretation": "timezone-naive values as provided; timezone not verified",
        }


def save_profile(report: dict, output_root: Path) -> Path:
    """集計値だけを新しい実行ディレクトリへ保存する。ユーザーIDは保存しない。"""
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    path = Path(
        tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-"), dir=root)
    )
    (path / "profile.json").write_text(serialized + "\n", encoding="utf-8")
    with (path / "daily_counts.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["day", "n_events"])
        writer.writeheader()
        writer.writerows(report["daily_counts"])
    lines = [
        "# Music4All-Onion データ調査",
        "",
        "全量の探索的集計。分割日は候補で、最終評価条件はまだ確定していません。",
        "",
        f"- 再生イベント: {report['n_events']:,}",
        f"- ユーザー: {report['n_users']:,}",
        f"- 楽曲: {report['n_items']:,}",
        f"- 時刻範囲: {report['min_time']} ～ {report['max_time']}",
        "- タイムゾーンは未確認。配布された時刻値をそのまま扱います。",
        "",
        f"- train終了境界: {report['boundaries']['train_end']}",
        f"- validation終了境界: {report['boundaries']['validation_end']}",
        "",
        "| 区分 | イベント数 | 比率 |",
        "| --- | ---: | ---: |",
    ]
    for label in ("train", "validation", "test"):
        lines.append(
            f"| {label} | {report['split_events'][label]:,} | {report['split_fractions'][label]:.4%} |"
        )
    lines += [
        "",
        "| 評価条件 | 未聴正解を持つユーザー | ユニーク正解ペア |",
        "| --- | ---: | ---: |",
    ]
    for label in ("validation", "test_train_only", "test_refit_train_validation"):
        phase = report[label]
        lines.append(f"| {label} | {phase['novel_users']:,} | {phase['novel_pairs']:,} |")
    lines += [
        "",
        "testの既聴判定にはvalidation期の履歴も含めます。Cold user → Cold item → 既聴 → 適格の順に、重複しない理由区分で数えています。",
        "",
        "詳細・出典チェックサム・学習履歴の分布は `profile.json` を参照してください。",
        "",
    ]
    (path / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile Music4All-Onion temporal listening events"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-end")
    parser.add_argument("--validation-end")
    args = parser.parse_args()
    try:
        print("Verifying and converting the source file", flush=True)
        convert_events(
            args.source, args.parquet, progress=lambda message: print(message, flush=True)
        )
        report = profile_events(
            args.parquet,
            train_end=args.train_end,
            validation_end=args.validation_end,
            progress=lambda message: print(message, flush=True),
        )
        saved = save_profile(report, args.output)
        print((saved / "summary.md").read_text(), flush=True)
        print(f"Saved: {saved}", flush=True)
    except (OSError, ValueError, ImportError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
