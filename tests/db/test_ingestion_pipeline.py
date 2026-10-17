"""合成の配布ファイルと実Parquet・実DBで共通取り込みを検証する。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db
LOCK = Path(__file__).resolve().parents[2] / "uv.lock"


def adapter(files):
    from src.adapters.music4all import Music4AllAdapter

    return Music4AllAdapter(
        record_path=files["record"],
        tags_path=files["tags"],
        events_path=files["events"],
        event_cache=files["cache"],
        version_label="v2",
        track_limit=3,
        user_limit=2,
    )


def run(db, root, a):
    from src.ingestion.pipeline import import_dataset

    return import_dataset(a, db, root, LOCK)


def counts(db):
    with db.connect() as c:
        return {
            name: c.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()
            for name in (
                "tracks",
                "users",
                "track_metadata",
                "metadata_snapshots",
                "import_runs",
                "data_artifacts",
            )
        }


def test_pipeline_publishes_verified_catalog_and_reuses_it_on_retry(
    db, music4all_factory, tmp_path
):
    from src.ingestion.pipeline import load_snapshot

    a = adapter(music4all_factory())
    root = tmp_path / "store"
    first = run(db, root, a)
    before = counts(db)
    second = run(db, root, a)
    after = counts(db)
    assert first.snapshot_id == second.snapshot_id
    assert first.run_id != second.run_id
    assert not first.reused and second.reused
    assert first.content_fingerprint == second.content_fingerprint
    assert after == {**before, "import_runs": 2}
    catalog = load_snapshot(db, root, first.snapshot_id)
    assert [t.source_id for t in catalog.tracks] == ["01", "02", "03"]
    assert [t.metadata for t in catalog.tracks] == [{"tags": {}}, {"tags": {"rock": 80}}, None]
    assert [u.source_id for u in catalog.users] == ["0002", "0010"]


def test_new_release_reuses_identity_and_unchanged_metadata(db, music4all_factory, tmp_path):
    root = tmp_path / "store"
    first = run(db, root, adapter(music4all_factory("v2")))
    second = run(
        db,
        root,
        adapter(
            music4all_factory(
                "v3", tag_rows=[("01", "{}"), ("02", "{'rock': 90}"), ("99", "{'pop': 50}")]
            )
        ),
    )
    assert first.snapshot_id != second.snapshot_id
    assert counts(db)["tracks"] == 3
    assert counts(db)["users"] == 2
    assert counts(db)["track_metadata"] == 3
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM dataset_versions")).scalar_one() == 2
        assert (
            c.execute(
                text("""SELECT count(DISTINCT track_metadata_id) FROM snapshot_tracks st
            JOIN tracks t ON t.id = st.track_id WHERE t.source_id = '01'""")
            ).scalar_one()
            == 1
        )


def test_adapter_failure_is_recorded_without_a_partial_snapshot(db, music4all_factory, tmp_path):
    from src.ingestion.pipeline import ImportFailed

    a = adapter(music4all_factory(tag_rows=[("02", "{'rock': 80}"), ("02", "{'rock': 90}")]))
    with pytest.raises(ImportFailed) as error:
        run(db, tmp_path / "store", a)
    assert error.value.error_type == "ValidationError"
    with db.connect() as c:
        assert c.execute(
            text("SELECT status, error_type, result_snapshot_id FROM import_runs")
        ).one() == ("failed", "ValidationError", None)
    assert counts(db)["metadata_snapshots"] == 0
    assert counts(db)["tracks"] == 0


def test_output_file_failure_is_recorded_before_publication(
    db, music4all_factory, tmp_path, monkeypatch
):
    from src.ingestion.artifacts import ArtifactStore
    from src.ingestion.pipeline import ImportFailed

    original = ArtifactStore.write_catalog

    def corrupt(store, catalog):
        files = original(store, catalog)
        files["tracks"].path.write_bytes(b"corrupted")
        return files

    monkeypatch.setattr(ArtifactStore, "write_catalog", corrupt)
    with pytest.raises(ImportFailed):
        run(db, tmp_path / "store", adapter(music4all_factory()))
    assert counts(db)["metadata_snapshots"] == 0
    with db.connect() as c:
        assert c.execute(text("SELECT status FROM import_runs")).scalar_one() == "failed"


def test_same_input_with_changed_output_fails_and_preserves_existing_snapshot(
    db, music4all_factory, tmp_path
):
    from src.ingestion.pipeline import ImportFailed, load_snapshot
    from src.ingestion.types import Catalog, TrackRecord

    root = tmp_path / "store"
    a = adapter(music4all_factory())
    first = run(db, root, a)
    original_load = a.load

    def changed(inputs):
        old = original_load(inputs)
        return Catalog(
            (old.tracks[0], TrackRecord("02", {"tags": {"rock": 90}}), old.tracks[2]),
            old.users,
            old.stats,
        )

    a.load = changed
    with pytest.raises(ImportFailed):
        run(db, root, a)
    assert counts(db)["metadata_snapshots"] == 1
    assert counts(db)["track_metadata"] == 2
    assert load_snapshot(db, root, first.snapshot_id).tracks[1].metadata == {"tags": {"rock": 80}}


def test_reader_rejects_corrupted_published_parquet(db, music4all_factory, tmp_path):
    from src.ingestion.pipeline import load_snapshot
    from src.ingestion.types import ValidationError

    root = tmp_path / "store"
    result = run(db, root, adapter(music4all_factory()))
    with db.connect() as c:
        key = c.execute(
            text("""SELECT a.storage_key FROM snapshot_artifacts sa
            JOIN data_artifacts a ON a.id = sa.artifact_id
            WHERE sa.snapshot_id = :s AND sa.role = 'tracks'"""),
            {"s": result.snapshot_id},
        ).scalar_one()
    (root / key).write_bytes(b"corrupted")
    with pytest.raises(ValidationError):
        load_snapshot(db, root, result.snapshot_id)


def test_pipeline_serializes_same_input_publishers(db, music4all_factory, tmp_path):
    root = tmp_path / "store"
    files = music4all_factory()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, db, root, adapter(files)) for _ in range(2)]
        results = [f.result(timeout=15) for f in futures]
    assert results[0].snapshot_id == results[1].snapshot_id
    assert sorted(r.reused for r in results) == [False, True]
    assert counts(db)["metadata_snapshots"] == 1
    assert counts(db)["import_runs"] == 2


def test_python_fingerprints_match_database_for_decimal_and_unicode_values(db):
    from src.ingestion.types import canonical_json, fingerprint

    value = {"音楽": [1.0, -0.0, 1e-7, 1e20, True, None], "a": 'escaped\n"'}
    with db.connect() as c:
        result = c.execute(
            text("SELECT recsys_json_sha256(CAST(:value AS jsonb))"),
            {"value": canonical_json(value)},
        ).scalar_one()
    assert result == fingerprint(value)
