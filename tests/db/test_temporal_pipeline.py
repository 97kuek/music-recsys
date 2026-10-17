from pathlib import Path

import pytest

pytestmark = pytest.mark.db


def test_snapshot_split_export_round_trip(db, music4all_factory, tmp_path):
    from src.adapters.music4all import Music4AllAdapter
    from src.data import load_dataset
    from src.ingestion.pipeline import import_dataset
    from src.ingestion.splits import load_split
    from src.temporal_cli import export_split, split_snapshot

    files = music4all_factory(
        events_data=b"user_id\ttrack_id\ttimestamp\na\t01\t2016-01-01 00:00:00\nb\t02\t2016-01-01 00:00:00\na\t02\t2018-01-01 00:00:00\nb\t01\t2020-01-01 00:00:00\n"
    )
    adapter = Music4AllAdapter(
        record_path=files["record"],
        tags_path=files["tags"],
        events_path=files["events"],
        event_cache=files["cache"],
        version_label="v2",
        track_limit=1,
        user_limit=2,
        selection="history",
        train_end="2017-03-29",
    )
    root = tmp_path / "store"
    lock = Path("uv.lock")
    snapshot = import_dataset(adapter, db, root, lock)
    split_id = split_snapshot(db, root, snapshot.snapshot_id, lockfile=lock)
    again = split_snapshot(db, root, snapshot.snapshot_id, lockfile=lock)
    assert split_id == again
    split = load_split(db, root, split_id)
    assert split["summary"]["evaluation"]["test"]["ground_truth_pairs"] == 1
    target = tmp_path / "test.json"
    export_split(db, root, split_id, target, phase="test")
    dataset = load_dataset(target, top_k=10)
    assert dataset.provenance["split_id"] == str(split_id)
    assert dataset.provenance["snapshot_id"] == str(snapshot.snapshot_id)
    assert len(dataset.train_interactions) == 3

    from src.listening_data import load_listening_dataset
    from src.listening_types import ListeningConfig

    bundle = tmp_path / "listening-test"
    export_split(db, root, split_id, bundle, phase="test", protocol="listening-v2")
    listening = load_listening_dataset(bundle, ListeningConfig())
    assert listening.manifest["source"]["split_id"] == str(split_id)
    assert listening.manifest["source"]["snapshot_id"] == str(snapshot.snapshot_id)
    assert len(listening.train_interactions) == 3
