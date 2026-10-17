"""保存済みデータの検証と、未取得タグ・空タグの区別を確認する。"""

import json
from pathlib import Path

import pytest

pytest.importorskip("pandas", reason="install the data dependency group")
from src import snapshot_analysis as analysis

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/examples/catalog/snapshot.json"


def test_saved_fixture_loads_frames_without_claiming_database_verification():
    snapshot = analysis.load_fixture(FIXTURE)
    assert snapshot.mode == "fixture"
    assert snapshot.overview["dataset_key"] == "synthetic-catalog"
    assert snapshot.overview["snapshot_id"] is None
    assert len(snapshot.tracks) == 4 and len(snapshot.users) == 2
    rows = analysis.validation_rows(snapshot, expected_tracks=4, expected_users=2)
    checks = {row["check"]: row for row in rows}
    assert checks["tracks_count"]["passed"] is True
    assert checks["users_count"]["passed"] is True
    assert checks["tracks_missing_tags"]["actual"] == 1
    assert checks["tracks_empty_tags"]["actual"] == 1
    assert checks["snapshot_published"]["passed"] is None
    assert checks["parquet_verified"]["passed"] is None


@pytest.mark.parametrize("field", ["source_id", "internal_id"])
def test_validation_detects_duplicate_track_ids(field):
    snapshot = analysis.load_fixture(FIXTURE)
    snapshot.tracks.loc[1, field] = snapshot.tracks.loc[0, field]
    checks = {row["check"]: row for row in analysis.validation_rows(snapshot)}
    assert checks[f"tracks_{field}_duplicates"]["actual"] == 1
    assert checks[f"tracks_{field}_duplicates"]["passed"] is False


def test_validation_detects_missing_id_and_wrong_counts():
    snapshot = analysis.load_fixture(FIXTURE)
    snapshot.users.loc[0, "source_id"] = None
    checks = {row["check"]: row for row in analysis.validation_rows(snapshot, expected_users=100)}
    assert checks["users_source_id_missing"]["actual"] == 1
    assert checks["users_source_id_missing"]["passed"] is False
    assert checks["users_count"]["passed"] is False
    assert all("user_id" not in row for row in checks.values())


def test_tag_eda_preserves_missing_empty_status_and_occurrence_rate():
    snapshot = analysis.load_fixture(FIXTURE)
    eda = analysis.snapshot_eda(snapshot)
    assert eda["tag_coverage"].to_dict("records") == [
        {"status": "missing", "n_tracks": 1},
        {"status": "empty", "n_tracks": 1},
        {"status": "present", "n_tracks": 2},
    ]
    assert eda["tags"].to_dict("records") == [
        {"tag": "rock", "n_tracks": 2, "mean_weight": 60.0, "track_fraction": 0.5},
        {"tag": "pop", "n_tracks": 1, "mean_weight": 20.0, "track_fraction": 0.25},
    ]
    assert eda["tag_counts"] == [2, 1, 0]
    assert eda["tag_weights"] == [80, 20, 40]


def test_saved_fixture_rejects_a_different_kind(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    raw["kind"] = "production_snapshot"
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="synthetic_catalog_fixture"):
        analysis.load_fixture(path)
