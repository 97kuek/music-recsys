"""保存済みsnapshotのSQL読込み・DataFrame検証・Parquet照合を実DBで確認する。"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


def saved_snapshot(db, music4all_factory, tmp_path):
    from src.infrastructure.datasets.music4all_adapter import Music4AllAdapter
    from src.infrastructure.ingestion.pipeline import import_dataset

    files = music4all_factory()
    adapter = Music4AllAdapter(
        record_path=files["record"],
        tags_path=files["tags"],
        events_path=files["events"],
        event_cache=files["cache"],
        version_label="v2",
        track_limit=3,
        user_limit=2,
    )
    root = tmp_path / "artifacts"
    result = import_dataset(adapter, db, root, ROOT / "uv.lock")
    return root, result.snapshot_id


def test_sql_reader_pins_snapshot_version_and_validates_frames(db, music4all_factory, tmp_path):
    from src.interfaces.notebook.snapshot_analysis import load_snapshot_frames, validation_rows

    root, snapshot_id = saved_snapshot(db, music4all_factory, tmp_path)
    snapshot = load_snapshot_frames(
        db, root, snapshot_id, dataset_key="music4all-onion", version_label="v2"
    )
    assert snapshot.mode == "postgresql"
    assert snapshot.overview["snapshot_id"] == str(snapshot_id)
    assert snapshot.overview["version_label"] == "v2"
    assert snapshot.overview["dataset_version_id"]
    assert len(snapshot.tracks) == 3 and len(snapshot.users) == 2
    assert snapshot.tracks["source_id"].tolist() == ["01", "02", "03"]
    checks = {
        row["check"]: row for row in validation_rows(snapshot, expected_tracks=3, expected_users=2)
    }
    assert checks["tracks_missing_tags"]["actual"] == 1
    assert checks["tracks_empty_tags"]["actual"] == 1
    assert all(row["passed"] is not False for row in checks.values())
    assert all(snapshot.database_checks.values())
    assert snapshot.overview["read_only"] is True
    with db.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM import_runs")).scalar_one() == 1


@pytest.mark.parametrize("dataset_key,version", [("other", "v2"), ("music4all-onion", "wrong")])
def test_sql_reader_rejects_wrong_dataset_or_version(
    db, music4all_factory, tmp_path, dataset_key, version
):
    from src.interfaces.notebook.snapshot_analysis import load_snapshot_frames

    root, snapshot_id = saved_snapshot(db, music4all_factory, tmp_path)
    with pytest.raises(ValueError, match="dataset/version"):
        load_snapshot_frames(db, root, snapshot_id, dataset_key=dataset_key, version_label=version)


def test_sql_reader_rejects_unknown_snapshot(db, tmp_path):
    from src.interfaces.notebook.snapshot_analysis import load_snapshot_frames

    with pytest.raises(ValueError, match="published snapshot"):
        load_snapshot_frames(
            db, tmp_path, uuid4(), dataset_key="music4all-onion", version_label="v2"
        )


def test_sql_reader_rejects_corrupted_parquet(db, music4all_factory, tmp_path):
    from src.interfaces.notebook.snapshot_analysis import load_snapshot_frames

    root, snapshot_id = saved_snapshot(db, music4all_factory, tmp_path)
    with db.connect() as connection:
        key = connection.execute(
            text("""SELECT a.storage_key FROM snapshot_artifacts sa
            JOIN data_artifacts a ON a.id = sa.artifact_id
            WHERE sa.snapshot_id = :snapshot_id AND sa.role = 'tracks' """),
            {"snapshot_id": snapshot_id},
        ).scalar_one()
    (root / key).write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        load_snapshot_frames(
            db, root, snapshot_id, dataset_key="music4all-onion", version_label="v2"
        )
