import pytest
from sqlalchemy import text
from test_snapshot_frames import saved_snapshot

pytestmark = pytest.mark.db


def test_snapshot_feature_export_preserves_payload_and_database(db, music4all_factory, tmp_path):
    from music_recsys.features import export_snapshot_features, load_features

    root, snapshot_id = saved_snapshot(db, music4all_factory, tmp_path)
    with db.connect() as c:
        before = c.execute(text("SELECT count(*) FROM import_runs")).scalar_one()
    output = tmp_path / "features.json"
    artifact = export_snapshot_features(db, root, snapshot_id, output)
    features = load_features(output, dataset_id="music4all-onion", snapshot_id=str(snapshot_id))
    assert features == {"02": {"rock": 80}}
    assert artifact["source"]["metadata_sha256"]
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM import_runs")).scalar_one() == before
    with pytest.raises(ValueError, match="出典"):
        load_features(output, dataset_id="music4all-onion", snapshot_id="other")
