"""COMMIT時の公開、失敗の隔離、再実行、公開後の変更拒否を検証する。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from alembic import command
from db_support import migration_config
from sqlalchemy.exc import IntegrityError
from test_schema import artifact, dataset, digest, execute, metadata, source_catalog, track

pytestmark = pytest.mark.db

CONFIG = {"track_limit": 2, "user_limit": 1, "seed": 0, "selection": "source_id"}


def input_document(c, cat, config=CONFIG):
    version = (
        execute(
            c,
            """SELECT d.key, v.source_ref, v.manifest_sha256, f.logical_name, a.sha256
        FROM dataset_versions v JOIN datasets d ON d.id = v.dataset_id
        JOIN source_files f ON f.dataset_version_id = v.id
        JOIN data_artifacts a ON a.id = f.artifact_id WHERE v.id = :v""",
            v=cat["version"],
        )
        .mappings()
        .one()
    )
    return {
        "dataset": version["key"],
        "dataset_version": {
            "source_ref": version["source_ref"],
            "manifest_sha256": version["manifest_sha256"],
        },
        "files": [{"name": version["logical_name"], "sha256": version["sha256"]}],
        "adapter": {"name": "music4all-onion", "version": "test-adapter-v1"},
        "pipeline_version": "test-pipeline-v1",
        "dependency_lock_sha256": "b" * 64,
        "config": config,
    }


def new_run(c, cat, sealed=True, config=CONFIG, fingerprint=None):
    import json

    doc = input_document(c, cat, config)
    fingerprint = fingerprint or digest(doc)
    run = execute(
        c,
        """INSERT INTO import_runs
        (dataset_id, dataset_version_id, adapter_name, adapter_version, pipeline_version,
         dependency_lock_sha256, config, input_fingerprint)
        VALUES (:d, :v, 'music4all-onion', 'test-adapter-v1', 'test-pipeline-v1',
                :lock, CAST(:config AS jsonb), :hash) RETURNING id""",
        d=cat["dataset"],
        v=cat["version"],
        lock="b" * 64,
        config=json.dumps(config),
        hash=fingerprint,
    ).scalar_one()
    execute(
        c,
        """INSERT INTO import_run_sources
        (dataset_id, dataset_version_id, import_run_id, source_file_id)
        VALUES (:d, :v, :r, :s)""",
        d=cat["dataset"],
        v=cat["version"],
        r=run,
        s=cat["source"],
    )
    if sealed:
        execute(
            c, "UPDATE import_runs SET input_sealed_at = clock_timestamp() WHERE id = :r", r=run
        )
    return {"id": run, "input": doc, "fingerprint": fingerprint}


def members(c, cat):
    d = cat["dataset"]
    t1 = track(c, d, "000Ab")
    t2 = track(c, d, "no-metadata")
    m = metadata(c, d, t1, {"tags": {}})
    u = execute(
        c, "INSERT INTO users (dataset_id, source_id) VALUES (:d, 'u1') RETURNING id", d=d
    ).scalar_one()
    a1 = artifact(c, d, checksum="c" * 64, rows=2, format="parquet")
    a2 = artifact(c, d, checksum="d" * 64, rows=1, format="parquet")
    return {
        "tracks": [(t1, m), (t2, None)],
        "users": [u],
        "artifacts": [("tracks", a1), ("users", a2)],
    }


def manifest(run):
    return {
        "input": run["input"],
        "tracks": [
            {"source_id": "000Ab", "metadata_sha256": digest({"tags": {}})},
            {"source_id": "no-metadata", "metadata_sha256": None},
        ],
        "users": ["u1"],
        "artifacts": {
            "tracks": {"sha256": "c" * 64, "row_count": 2},
            "users": {"sha256": "d" * 64, "row_count": 1},
        },
    }


def snapshot(c, cat, run, records, document=None, publish=True):
    import json

    document = manifest(run) if document is None else document
    content_hash = digest({k: v for k, v in document.items() if k != "input"})
    s = execute(
        c,
        """INSERT INTO metadata_snapshots
        (dataset_id, dataset_version_id, created_by_import_run_id, input_fingerprint,
         content_fingerprint, manifest)
        VALUES (:d, :v, :r, :hash, :content, CAST(:manifest AS jsonb)) RETURNING id""",
        d=cat["dataset"],
        v=cat["version"],
        r=run["id"],
        hash=run["fingerprint"],
        content=content_hash,
        manifest=json.dumps(document),
    ).scalar_one()
    for t, m in records["tracks"]:
        execute(
            c,
            """INSERT INTO snapshot_tracks
            (dataset_id, snapshot_id, track_id, track_metadata_id) VALUES (:d, :s, :t, :m)""",
            d=cat["dataset"],
            s=s,
            t=t,
            m=m,
        )
    for u in records["users"]:
        execute(
            c,
            "INSERT INTO snapshot_users (dataset_id, snapshot_id, user_id) VALUES (:d, :s, :u)",
            d=cat["dataset"],
            s=s,
            u=u,
        )
    for role, a in records["artifacts"]:
        execute(
            c,
            """INSERT INTO snapshot_artifacts
            (dataset_id, snapshot_id, role, artifact_id) VALUES (:d, :s, :role, :a)""",
            d=cat["dataset"],
            s=s,
            role=role,
            a=a,
        )
    if publish:
        succeed(c, run, s)
        execute(
            c, "UPDATE metadata_snapshots SET published_at = clock_timestamp() WHERE id = :s", s=s
        )
    return s


def succeed(c, run, snapshot_id):
    execute(
        c,
        """UPDATE import_runs SET status = 'succeeded', finished_at = clock_timestamp(),
        result_snapshot_id = :s WHERE id = :r""",
        s=snapshot_id,
        r=run["id"],
    )


def prepared(db):
    with db.begin() as c:
        cat = source_catalog(c)
        records = members(c, cat)
        run = new_run(c, cat)
    return cat, records, run


def test_unsealed_run_cannot_commit(db):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            new_run(c, source_catalog(c), sealed=False)


def test_input_hash_must_match_actual_sources_and_configuration(db):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            new_run(c, source_catalog(c), fingerprint="0" * 64)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE import_runs SET config = '{}'",
        "UPDATE import_runs SET input_sealed_at = NULL",
        "DELETE FROM import_run_sources",
        "UPDATE import_run_sources SET source_file_id = source_file_id",
        "DELETE FROM import_runs",
    ],
)
def test_sealed_inputs_and_run_identity_cannot_change(db, statement):
    prepared(db)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(c, statement)


def test_unpublished_snapshot_cannot_commit(db):
    cat, records, run = prepared(db)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            snapshot(c, cat, run, records, publish=False)
    with db.begin() as c:
        assert execute(c, "SELECT count(*) FROM metadata_snapshots").scalar_one() == 0
        assert execute(c, "SELECT status FROM import_runs").scalar_one() == "running"


def test_atomic_publication_freezes_members_and_distinguishes_missing_metadata(db):
    cat, records, run = prepared(db)
    with db.begin() as c:
        s = snapshot(c, cat, run, records)
    with db.begin() as c:
        state = (
            execute(
                c,
                """SELECT r.status, r.result_snapshot_id = s.id AS same,
            s.published_at IS NOT NULL AS published FROM import_runs r
            JOIN metadata_snapshots s ON s.id = r.result_snapshot_id""",
            )
            .mappings()
            .one()
        )
        assert dict(state) == {"status": "succeeded", "same": True, "published": True}
        assert execute(c, "SELECT recsys_snapshot_manifest(:s)", s=s).scalar_one() == manifest(run)
        assert execute(
            c,
            '''SELECT m.payload FROM snapshot_tracks st
            JOIN tracks t ON t.id = st.track_id LEFT JOIN track_metadata m ON m.id = st.track_metadata_id
            ORDER BY t.source_id COLLATE "C"''',
        ).scalars().all() == [{"tags": {}}, None]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE metadata_snapshots SET manifest = '{}'",
        "UPDATE metadata_snapshots SET published_at = NULL",
        "DELETE FROM metadata_snapshots",
        "DELETE FROM snapshot_tracks",
        "UPDATE snapshot_tracks SET track_metadata_id = NULL",
        "DELETE FROM snapshot_users",
        "DELETE FROM snapshot_artifacts",
        "UPDATE import_runs SET stats = '{}'",
    ],
)
def test_published_snapshot_and_finished_run_are_immutable(db, statement):
    cat, records, run = prepared(db)
    with db.begin() as c:
        snapshot(c, cat, run, records)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(c, statement)


@pytest.mark.parametrize("component", ["tracks", "users", "artifacts", "input"])
def test_manifest_cannot_claim_members_or_artifacts_that_are_not_registered(db, component):
    cat, records, run = prepared(db)
    wrong = manifest(run)
    wrong[component] = [] if component in ("tracks", "users") else {}
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            snapshot(c, cat, run, records, document=wrong)


def test_metadata_reference_must_belong_to_the_exact_track(db):
    cat, records, run = prepared(db)
    wrong = {**records, "tracks": [(records["tracks"][1][0], records["tracks"][0][1])]}
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            snapshot(c, cat, run, wrong)


@pytest.mark.parametrize("component", ["tracks", "users", "artifacts"])
def test_snapshot_members_cannot_mix_datasets(db, component):
    cat, records, run = prepared(db)
    with db.begin() as c:
        other = source_catalog(c, "talkplay")
        other_records = members(c, other)
    wrong = {**records, component: other_records[component]}
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            snapshot(c, cat, run, wrong)


def test_failed_publication_leaves_no_usable_partial_result(db):
    cat, records, run = prepared(db)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            snapshot(c, cat, run, records, publish=False)
            execute(c, "DELETE FROM snapshot_users")
            succeed(c, run, execute(c, "SELECT id FROM metadata_snapshots").scalar_one())
            execute(c, "UPDATE metadata_snapshots SET published_at = clock_timestamp()")
    with db.begin() as c:
        execute(
            c,
            """UPDATE import_runs SET status = 'failed', finished_at = clock_timestamp(),
            error_type = 'ValidationError' WHERE id = :r""",
            r=run["id"],
        )
    with db.begin() as c:
        assert execute(c, "SELECT count(*) FROM metadata_snapshots").scalar_one() == 0
        assert execute(c, "SELECT count(*) FROM snapshot_tracks").scalar_one() == 0
        assert execute(c, "SELECT status, result_snapshot_id FROM import_runs").one() == (
            "failed",
            None,
        )


def test_reimport_records_new_run_and_reuses_existing_snapshot(db):
    cat, records, run = prepared(db)
    with db.begin() as c:
        s = snapshot(c, cat, run, records)
    with db.begin() as c:
        retry = new_run(c, cat)
        succeed(c, retry, s)
    with db.begin() as c:
        assert execute(c, "SELECT count(*) FROM import_runs").scalar_one() == 2
        assert execute(c, "SELECT count(*) FROM metadata_snapshots").scalar_one() == 1
        assert execute(c, "SELECT count(*) FROM tracks").scalar_one() == 2
        assert execute(c, "SELECT count(*) FROM track_metadata").scalar_one() == 1
        assert (
            execute(c, "SELECT count(DISTINCT result_snapshot_id) FROM import_runs").scalar_one()
            == 1
        )


def test_successful_retry_cannot_use_snapshot_from_different_inputs(db):
    cat, records, run = prepared(db)
    with db.begin() as c:
        s = snapshot(c, cat, run, records)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            retry = new_run(c, cat, config={**CONFIG, "seed": 1})
            succeed(c, retry, s)


def test_uncommitted_snapshot_is_invisible_then_late_insertion_is_rejected(db):
    cat, records, run = prepared(db)
    with db.connect() as writer, db.connect() as reader:
        with writer.begin():
            s = snapshot(writer, cat, run, records)
            assert execute(reader, "SELECT count(*) FROM metadata_snapshots").scalar_one() == 0
        assert execute(reader, "SELECT count(*) FROM metadata_snapshots").scalar_one() == 1
        reader.rollback()
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            t = track(c, cat["dataset"], "late-track")
            execute(
                c,
                """INSERT INTO snapshot_tracks (dataset_id, snapshot_id, track_id)
                VALUES (:d, :s, :t)""",
                d=cat["dataset"],
                s=s,
                t=t,
            )


def test_same_input_concurrent_publication_has_one_snapshot(db):
    cat, records, first = prepared(db)
    with db.begin() as c:
        second = new_run(c, cat)
    started = Event()
    worker_pid = []

    def competing_publisher():
        with db.begin() as c:
            worker_pid.append(execute(c, "SELECT pg_backend_pid()").scalar_one())
            started.set()
            snapshot(c, cat, second, records)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.begin() as c:
            s = snapshot(c, cat, first, records)
            future = pool.submit(competing_publisher)
            assert started.wait(3)
            deadline = monotonic() + 3
            while (
                execute(
                    c,
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid",
                    pid=worker_pid[0],
                ).scalar_one()
                != "Lock"
            ):
                assert monotonic() < deadline, "Competing publisher did not wait on the unique key"
                assert not future.done(), "Competing publisher finished before the first commit"
                execute(c, "SELECT pg_stat_clear_snapshot()")
                sleep(0.01)
        with pytest.raises(IntegrityError):
            future.result(timeout=10)
    with db.begin() as c:
        succeed(c, second, s)
    with db.begin() as c:
        assert execute(c, "SELECT count(*) FROM metadata_snapshots").scalar_one() == 1
        assert (
            execute(c, "SELECT count(*) FROM import_runs WHERE status = 'succeeded'").scalar_one()
            == 2
        )


def test_migration_roundtrip_is_limited_to_test_schema(db):
    with db.begin() as c:
        schema = execute(c, "SELECT current_schema()").scalar_one()
        command.downgrade(migration_config(c), "0001")
        assert execute(c, "SELECT to_regclass(:name)", name=f"{schema}.tracks").scalar_one() is None
        command.upgrade(migration_config(c), "head")
        track(c, dataset(c))
        assert execute(c, "SELECT version_num FROM alembic_version").scalar_one() == "0002"


@pytest.mark.parametrize("table", ["snapshot_tracks", "snapshot_users", "snapshot_artifacts"])
def test_truncate_cannot_bypass_published_snapshot_immutability(db, table):
    cat, records, run = prepared(db)
    with db.begin() as c:
        snapshot(c, cat, run, records)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(c, f"TRUNCATE {table}")


@pytest.mark.parametrize("format,rows", [("parquet", 3), ("bz2", 2)])
def test_output_artifact_must_be_parquet_with_actual_membership_count(db, format, rows):
    cat, records, run = prepared(db)
    with db.begin() as c:
        a = artifact(c, cat["dataset"], checksum="e" * 64, rows=rows, format=format)
    wrong_records = {**records, "artifacts": [("tracks", a), records["artifacts"][1]]}
    document = manifest(run)
    document["artifacts"]["tracks"] = {"sha256": "e" * 64, "row_count": rows}
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            snapshot(c, cat, run, wrong_records, document=document)
