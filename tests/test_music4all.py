"""全体時系列の集計と、予測時点より前の履歴による未聴判定を検証する。"""

import bz2
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("duckdb", reason="install the data dependency group")
pytest.importorskip("pyarrow", reason="install the data dependency group")


def write_events(path: Path, rows: list[tuple[str, str, str]]) -> None:
    with bz2.open(path, "wt") as stream:
        stream.write("user_id\ttrack_id\ttimestamp\n")
        for user, item, timestamp in rows:
            stream.write(f"{user}\t{item}\t{timestamp}\n")


@pytest.fixture
def events(tmp_path):
    path = tmp_path / "events.tsv.bz2"
    # 行順は時系列ではない。a/b/cがtrainのカタログ、dはvalidationで初出。
    write_events(
        path,
        [
            ("u1", "c", "2020-01-05 00:00:00"),
            ("u1", "a", "2020-01-01 00:00:00"),
            ("u1", "a", "2020-01-02 00:00:00"),
            ("u2", "b", "2020-01-01 00:00:00"),
            ("u2", "c", "2020-01-02 00:00:00"),
            ("u1", "b", "2020-01-03 00:00:00"),
            ("u1", "d", "2020-01-04 00:00:00"),
            ("cold", "a", "2020-01-03 00:00:00"),
            ("u1", "b", "2020-01-06 00:00:00"),
            ("u2", "a", "2020-01-05 00:00:00"),
            ("new", "c", "2020-01-06 00:00:00"),
            ("u2", "e", "2020-01-06 00:00:00"),
            ("u2", "d", "2020-01-06 00:00:00"),
            ("cold", "b", "2020-01-06 00:00:00"),
        ],
    )
    return path


def test_conversion_preserves_all_events_and_has_source_fingerprint(events, tmp_path):
    import duckdb

    from src.infrastructure.datasets.music4all import convert_events

    target = tmp_path / "events.parquet"
    manifest = convert_events(events, target)
    assert manifest["n_events"] == 14
    assert len(manifest["source_sha256"]) == 64
    with duckdb.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM read_parquet(?)", [str(target)]).fetchone()[0]
            == 14
        )
        assert connection.execute(
            "SELECT min(event_time) FROM read_parquet(?)", [str(target)]
        ).fetchone()[0] == datetime(2020, 1, 1)


def test_conversion_does_not_reuse_cache_for_changed_source(events, tmp_path):
    from src.infrastructure.datasets.music4all import convert_events

    target = tmp_path / "events.parquet"
    convert_events(events, target)
    original = target.read_bytes()
    write_events(events, [("other", "x", "2020-01-01 00:00:00")])
    with pytest.raises(ValueError, match="source"):
        convert_events(events, target)
    assert target.read_bytes() == original


def test_conversion_rejects_modified_parquet_cache(events, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.infrastructure.datasets.music4all import convert_events

    target = tmp_path / "events.parquet"
    convert_events(events, target)
    table = pq.read_table(target)
    changed = table.set_column(1, "item_id", pa.array(["changed"] * table.num_rows))
    pq.write_table(changed, target)
    with pytest.raises(ValueError, match="cache"):
        convert_events(events, target)


def test_global_split_counts_and_novel_truth_exclude_validation_history(events, tmp_path):
    from src.infrastructure.datasets.music4all import convert_events, profile_events

    target = tmp_path / "events.parquet"
    convert_events(events, target)
    report = profile_events(target, train_end="2020-01-03", validation_end="2020-01-05")
    assert report["n_events"] == 14
    assert report["n_users"] == 4
    assert report["n_items"] == 5
    assert report["split_events"] == {"train": 4, "validation": 3, "test": 7}
    assert report["train"]["n_users"] == 2
    assert report["train"]["n_items"] == 3
    assert report["train"]["unique_tracks_quantiles"]["0.5"] == 1.5
    assert report["validation"]["novel_users"] == 1
    assert report["validation"]["novel_pairs"] == 1
    test = report["test_train_only"]
    assert test["novel_users"] == 2
    assert test["novel_pairs"] == 2
    assert test["pair_reasons"] == {"eligible": 2, "cold_user": 2, "cold_item": 2, "seen": 1}
    # u1/bはtrainにはないがvalidationで聴いているため、testの未聴正解ではない。
    assert report["test_refit_train_validation"]["novel_pairs"] == 4
    assert report["test_refit_train_validation"]["novel_users"] == 3


def test_daily_boundary_proposal_keeps_same_day_events_together(tmp_path):
    from src.infrastructure.datasets.music4all import convert_events, profile_events

    path = tmp_path / "days.tsv.bz2"
    write_events(path, [("u", "a", f"2020-01-{day:02d} 12:00:00") for day in range(1, 11)])
    target = tmp_path / "days.parquet"
    convert_events(path, target)
    report = profile_events(target)
    assert report["boundaries"] == {
        "train_end": "2020-01-09T00:00:00",
        "validation_end": "2020-01-10T00:00:00",
    }
    assert report["split_events"] == {"train": 8, "validation": 1, "test": 1}


def test_bad_timestamp_does_not_publish_partial_cache(tmp_path):
    from src.infrastructure.datasets.music4all import convert_events

    source = tmp_path / "bad.tsv.bz2"
    write_events(source, [("u", "a", "not-a-time")])
    target = tmp_path / "bad.parquet"
    with pytest.raises(ValueError):
        convert_events(source, target)
    assert not target.exists()


def test_equal_or_reversed_boundaries_are_rejected(events, tmp_path):
    from src.infrastructure.datasets.music4all import convert_events, profile_events

    target = tmp_path / "events.parquet"
    convert_events(events, target)
    with pytest.raises(ValueError, match="boundaries"):
        profile_events(target, train_end="2020-01-05", validation_end="2020-01-05")


def test_profile_save_preserves_existing_runs_and_contains_provenance(events, tmp_path):
    from src.infrastructure.datasets.music4all import convert_events, profile_events, save_profile

    target = tmp_path / "events.parquet"
    convert_events(events, target)
    report = profile_events(target, train_end="2020-01-03", validation_end="2020-01-05")
    first = save_profile(report, tmp_path / "runs")
    second = save_profile(report, tmp_path / "runs")
    assert first != second
    saved = json.loads((first / "profile.json").read_text())
    assert saved["test_train_only"]["novel_users"] == 2
    assert saved["provenance"]["source_sha256"]
    assert (first / "daily_counts.csv").is_file()
    assert (first / "summary.md").is_file()


def test_profile_cli_runs_from_explicit_paths(events, tmp_path):
    target = tmp_path / "events.parquet"
    output = tmp_path / "runs"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.infrastructure.datasets.music4all",
            "--source",
            str(events),
            "--parquet",
            str(target),
            "--output",
            str(output),
            "--train-end",
            "2020-01-03",
            "--validation-end",
            "2020-01-05",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    profiles = list(output.glob("*/profile.json"))
    assert len(profiles) == 1
    assert json.loads(profiles[0].read_text())["split_events"]["test"] == 7
