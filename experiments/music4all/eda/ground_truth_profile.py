"""Train／Validationの再生回数・再生日数を集計する研究用スクリプト。

Testの統計やモデル評価は実行しない。出力先は新しいディレクトリに限る。
実行: .venv/bin/python experiments/music4all/eda/ground_truth_profile.py
小規模確認: 上記コマンドに --self-check を追加。出力先指定: --output PATH。
"""

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
START = datetime(2017, 3, 29)
END = datetime(2018, 9, 21)
WINDOWS = {"30d": 30, "90d": 90, "180d": 180, "full": (END - START).days}
QUANTILES = [0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99, 1]


def partition_events(c, source, destination):
    c.execute(
        """COPY (SELECT user_id, item_id, event_time, hash(user_id)%32 AS bucket
        FROM read_parquet($source) WHERE event_time < $end) TO $destination
        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY(bucket))""",
        {"source": str(source), "end": END, "destination": str(destination)},
    )


def aggregate_train(c):
    return c.sql(
        """SELECT user_id, item_id, count(*)::BIGINT AS play_count,
        count(DISTINCT CAST(event_time AS DATE))::BIGINT AS active_days
        FROM events WHERE event_time < $start GROUP BY user_id, item_id""",
        params={"start": START},
    )


def aggregate_validation(c):
    return c.sql(
        """SELECT user_id, item_id, CAST(event_time AS DATE) AS day,
        count(*)::BIGINT AS day_plays FROM events
        WHERE event_time >= $start AND event_time < $end
        GROUP BY user_id, item_id, day""",
        params={"start": START, "end": END},
    )


def window_pairs(c, days):
    return c.sql(
        """SELECT user_id, item_id, sum(day_plays)::BIGINT AS play_count,
        count(*)::BIGINT AS active_days, min(day) AS first_day
        FROM validation_daily WHERE day < $end GROUP BY user_id, item_id""",
        params={"end": START + timedelta(days=days)},
    )


def classify(c):
    return c.sql("""SELECT p.*, coalesce(t.play_count, 0) AS train_play_count,
        coalesce(t.active_days, 0) AS train_active_days,
        CASE WHEN u.user_id IS NULL THEN 'cold_user'
        WHEN i.item_id IS NULL THEN 'cold_item'
        WHEN t.user_id IS NULL THEN 'new' ELSE 'repeat' END AS category
        FROM pairs p LEFT JOIN train_pairs t USING (user_id, item_id)
        LEFT JOIN train_users u USING (user_id)
        LEFT JOIN train_items i USING (item_id)""")


def summarize(c, relation):
    # relation is a name created by this script, never external input.
    row = c.execute(
        f"""SELECT count(*), count(DISTINCT user_id), sum(play_count),
        quantile_disc(play_count, ?), quantile_disc(active_days, ?),
        corr(ln(1 + play_count), ln(1 + active_days)),
        count(*) FILTER(WHERE play_count >= 100),
        count(*) FILTER(WHERE play_count >= 100 AND active_days = 1)
        FROM {relation}""",
        [QUANTILES, QUANTILES],
    ).fetchone()
    pairs, users, events, counts, days, correlation, high_count, high_one_day = row
    thresholds = [1, 2, 3, 5, 10, 20, 50, 100]
    tails = {}
    for field in ("play_count", "active_days"):
        values = c.execute(
            f"SELECT {', '.join(f'count(*) FILTER(WHERE {field} >= {n})' for n in thresholds)} FROM {relation}"
        ).fetchone()
        tails[field] = dict(zip(map(str, thresholds), values))
    user_stats = c.execute(
        f"""SELECT quantile_disc(n, ?),
        quantile_disc(multiday, ?), avg(multiday::DOUBLE/n)
        FROM (SELECT user_id, count(*) AS n,
        count(*) FILTER(WHERE active_days >= 2) AS multiday
        FROM {relation} GROUP BY user_id)""",
        [QUANTILES, QUANTILES],
    ).fetchone()
    cross = c.execute(f"""SELECT CASE WHEN play_count=1 THEN '1'
        WHEN play_count<5 THEN '2-4' WHEN play_count<10 THEN '5-9'
        WHEN play_count<100 THEN '10-99' ELSE '100+' END AS plays,
        CASE WHEN active_days=1 THEN '1' WHEN active_days<5 THEN '2-4'
        WHEN active_days<10 THEN '5-9' ELSE '10+' END AS days,
        count(*) FROM {relation} GROUP BY plays, days ORDER BY plays, days""").fetchall()
    checks = c.execute(f"""SELECT count(*) FILTER(WHERE play_count < 1 OR
        active_days < 1 OR active_days > play_count) FROM {relation}""").fetchone()[0]
    assert checks == 0, "active_days must be in [1, play_count]"
    return {
        "pairs": pairs,
        "users_with_positive_pairs": users,
        "events": int(events or 0),
        "play_count_quantiles": dict(zip(map(str, QUANTILES), counts or [])),
        "active_days_quantiles": dict(zip(map(str, QUANTILES), days or [])),
        "log_count_day_correlation": correlation,
        "count_100_plus_pairs": high_count,
        "count_100_plus_single_day_pairs": high_one_day,
        "threshold_counts": tails,
        "per_user_positive_pairs_quantiles": dict(zip(map(str, QUANTILES), user_stats[0] or [])),
        "per_user_multiday_pairs_quantiles": dict(zip(map(str, QUANTILES), user_stats[1] or [])),
        "user_macro_multiday_fraction_among_positive_users": user_stats[2],
        "play_day_cross_counts": [{"plays": p, "days": d, "pairs": n} for p, d, n in cross],
    }


def retention(c, days):
    end = START + timedelta(days=days)
    c.sql(
        """SELECT p.user_id, p.item_id, p.first_day,
        sum(d.day_plays)::BIGINT AS play_count, count(*)::BIGINT AS active_days
        FROM classified p JOIN validation_daily d USING (user_id, item_id)
        WHERE p.category = 'new' AND p.first_day + INTERVAL 30 DAY <= $end
        AND d.day < p.first_day + INTERVAL 30 DAY
        GROUP BY p.user_id, p.item_id, p.first_day""",
        params={"end": end},
    ).create_view("followup", replace=True)
    result = summarize(c, "followup")
    result["followup_calendar_days"] = 30
    result["late_first_play_excluded_pairs"] = c.execute(
        """SELECT count(*) FROM classified
        WHERE category='new' AND first_day + INTERVAL 30 DAY > ?""",
        [end],
    ).fetchone()[0]
    return result


def self_check():
    # Hand-checked observations: 01 is new and heard on two dates, 02 is repeat;
    # 03 is cold item, cold user has priority even when the item is also cold.
    with duckdb.connect() as c:
        c.execute("CREATE TABLE events(user_id VARCHAR,item_id VARCHAR,event_time TIMESTAMP)")
        c.execute("""INSERT INTO events VALUES
            ('u','02','2017-03-28 10:00:00'), ('v','01','2017-03-28 10:00:00'),
            ('u','01','2017-03-29 00:00:00'), ('u','01','2017-03-29 12:00:00'),
            ('u','01','2017-03-30 10:00:00'), ('u','02','2017-04-01 10:00:00'),
            ('u','03','2017-04-01 10:00:00'), ('cold','03','2017-04-01 10:00:00'),
            ('u','01','2018-09-21 00:00:00')""")
        aggregate_train(c).create_view("train_pairs")
        c.execute("CREATE VIEW train_users AS SELECT DISTINCT user_id FROM train_pairs")
        c.execute("CREATE VIEW train_items AS SELECT DISTINCT item_id FROM train_pairs")
        aggregate_validation(c).create_view("validation_daily")
        window_pairs(c, 30).create_view("pairs")
        classify(c).create_view("classified")
        actual = c.execute(
            "SELECT user_id,item_id,play_count,active_days,category FROM classified ORDER BY user_id,item_id"
        ).fetchall()
        assert actual == [
            ("cold", "03", 1, 1, "cold_user"),
            ("u", "01", 3, 2, "new"),
            ("u", "02", 1, 1, "repeat"),
            ("u", "03", 1, 1, "cold_item"),
        ], actual
        summary = summarize(c, "classified")
        assert summary["pairs"] == 4 and summary["events"] == 6
        r = retention(c, 30)
        assert r["pairs"] == 1 and r["events"] == 3
        assert r["threshold_counts"]["active_days"]["2"] == 1
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "events.parquet"
            destination = Path(tmp) / "partitions"
            c.execute("COPY events TO ? (FORMAT PARQUET)", [str(source)])
            partition_events(c, source, destination)
            observed = c.execute(
                "SELECT count(*), max(event_time) < ? FROM read_parquet(?)",
                [END, str(destination / "**/*.parquet")],
            ).fetchone()
            assert observed == (8, True), observed
        print("Synthetic boundary/count/day/cold-start/30-day-followup checks passed", flush=True)


def main():
    self_check()
    output = (
        Path(sys.argv[sys.argv.index("--output") + 1])
        if "--output" in sys.argv
        else ROOT / "data/ground-truth-eda" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / "data/processed/music4all-onion-v2/events.parquet"
    manifest = json.loads(source.with_suffix(".manifest.json").read_text())
    print("Verifying event cache SHA-256", flush=True)
    with source.open("rb") as stream:
        actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    assert actual_hash == manifest["parquet_sha256"]
    (output / "analysis.py").write_text(Path(__file__).read_text())
    result = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "duckdb_version": duckdb.__version__,
        "source_parquet_sha256": actual_hash,
        "source_manifest": manifest,
        "train_end": START.isoformat(),
        "validation_end": END.isoformat(),
        "timezone": "source-naive calendar dates; no UTC or JST conversion",
        "test_used_for_analysis": False,
        "quantile_method": "exact quantile_disc; positive user-item pairs",
        "resources": {"memory_limit": "2GB", "threads": 2, "user_hash_partitions": 32},
        "windows": {},
    }
    with duckdb.connect(
        str(output / "working.duckdb"),
        config={
            "memory_limit": "2GB",
            "threads": 2,
            "preserve_insertion_order": False,
            "temp_directory": str(output / "spill"),
            "max_temp_directory_size": "40GB",
        },
    ) as c:
        print(
            "Partitioning Train/Validation events by user (32 partitions; no Test rows)", flush=True
        )
        partition_events(c, source, output / "partitions")
        c.execute(
            "CREATE TABLE train_pairs(user_id VARCHAR,item_id VARCHAR,play_count BIGINT,active_days BIGINT)"
        )
        c.execute(
            "CREATE TABLE validation_daily(user_id VARCHAR,item_id VARCHAR,day DATE,day_plays BIGINT)"
        )
        for bucket in range(32):
            c.read_parquet(
                str(output / "partitions" / f"bucket={bucket}" / "*.parquet")
            ).create_view("events", replace=True)
            aggregate_train(c).create_view("train_aggregate", replace=True)
            c.execute("INSERT INTO train_pairs SELECT * FROM train_aggregate")
            aggregate_validation(c).create_view("daily_aggregate", replace=True)
            c.execute("INSERT INTO validation_daily SELECT * FROM daily_aggregate")
            print(f"Aggregated user partition {bucket + 1}/32", flush=True)
        c.execute("CREATE TABLE train_users AS SELECT DISTINCT user_id FROM train_pairs")
        c.execute("CREATE TABLE train_items AS SELECT DISTINCT item_id FROM train_pairs")
        result["train_users"] = c.execute("SELECT count(*) FROM train_users").fetchone()[0]
        result["train_items"] = c.execute("SELECT count(*) FROM train_items").fetchone()[0]
        result["train"] = summarize(c, "train_pairs")
        assert result["train"]["events"] == 202396019
        assert result["train_users"] == 119133 and result["train_items"] == 45888
        for label, days in WINDOWS.items():
            print(f"Profiling Validation {label} ({days} days)", flush=True)
            window_pairs(c, days).create_view("pairs", replace=True)
            classify(c).create_view("classified", replace=True)
            reasons = {
                category: {"pairs": n, "events": int(events), "users": users}
                for category, n, events, users in c.execute(
                    "SELECT category,count(*),sum(play_count),count(DISTINCT user_id) FROM classified GROUP BY category"
                ).fetchall()
            }
            r = {
                "days": days,
                "end_exclusive": (START + timedelta(days=days)).isoformat(),
                "reason_counts": reasons,
                "categories": {},
            }
            for category in ("new", "repeat"):
                c.sql(
                    "SELECT * FROM classified WHERE category=$category",
                    params={"category": category},
                ).create_view("selected", replace=True)
                r["categories"][category] = summarize(c, "selected")
            r["new_fixed_30day_followup"] = retention(c, days)
            assert (
                sum(x["events"] for x in reasons.values())
                == c.execute("SELECT sum(play_count) FROM pairs").fetchone()[0]
            )
            if label == "full":
                assert reasons["new"]["pairs"] == 2874931
                assert reasons["repeat"]["pairs"] == 3326807
                assert sum(x["events"] for x in reasons.values()) == 25281875
                assert reasons["cold_user"]["pairs"] == 207
                assert reasons["cold_item"]["pairs"] == 1060658
            result["windows"][label] = r
            (output / "profile.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            )
            print(
                f"  new={reasons.get('new', {}).get('pairs', 0):,}; repeat={reasons.get('repeat', {}).get('pairs', 0):,}",
                flush=True,
            )
    result["checks"] = {
        "synthetic_checks_passed": True,
        "source_hash_matches": True,
        "full_validation_matches_existing_eda": True,
        "active_days_lte_play_count": True,
        "test_event_values_not_aggregated": True,
    }
    (output / "profile.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(f"Completed: {output / 'profile.json'}", flush=True)


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    else:
        main()
