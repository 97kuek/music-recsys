"""共通取り込みと、公開済みsnapshotに限定した読込み。"""

import hashlib
import json
import platform
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from music_recsys.ingestion.artifacts import WRITER_OPTIONS, ArtifactStore
from music_recsys.ingestion.postgres import PostgresStore, execute
from music_recsys.ingestion.types import (
    Artifact,
    Catalog,
    DatasetAdapter,
    ImportFailed,
    ImportResult,
    TrackRecord,
    UserRecord,
    ValidationError,
    canonical_json,
    source_version,
    validate_catalog,
)


def import_dataset(
    adapter: DatasetAdapter,
    engine,
    storage_root: Path,
    lockfile: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> ImportResult:
    import duckdb
    import pyarrow as pa

    report = progress or (lambda message: None)
    release = adapter.release
    store = ArtifactStore(storage_root, release.key)
    database = PostgresStore(engine)
    sources = {}
    for index, spec in enumerate(release.sources, 1):
        report(f"Verifying source {index}/{len(release.sources)}")
        if spec.name in sources:
            raise ValidationError("duplicated source file name")
        sources[spec.name] = store.put(
            spec.path,
            format=spec.format,
            expected_size=spec.byte_size,
            checksum_algorithm=spec.checksum_algorithm,
            expected_checksum=spec.checksum,
        )
    inputs = adapter.prepare(store, sources)
    config = json.loads(canonical_json(inputs.config))
    config.update(
        {
            "writer": {"schema_version": 1, "pyarrow_version": pa.__version__, **WRITER_OPTIONS},
            "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__},
            "schema_revisions": database.revisions(),
        }
    )
    pipeline_version = source_version(
        Path(__file__).parent.parent,
        (
            "ingestion/types.py",
            "ingestion/artifacts.py",
            "ingestion/postgres.py",
            "ingestion/pipeline.py",
        ),
    )
    with Path(lockfile).open("rb") as stream:
        lock_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    report("Starting import with sealed input provenance")
    started = database.start(
        release,
        sources,
        inputs.artifacts,
        config,
        adapter.name,
        adapter.version,
        pipeline_version,
        lock_sha,
    )
    try:
        catalog = validate_catalog(adapter.load(inputs))
        report("Writing and verifying catalog Parquet files")
        outputs = store.write_catalog(catalog)
        for a in (*sources.values(), *inputs.artifacts):
            store.verify(a)
        store.validate_catalog_files(outputs, catalog)
        report("Publishing snapshot")
        snapshot_id, reused, content_sha = database.publish(started, catalog, outputs)
        return ImportResult(
            started.run_id,
            snapshot_id,
            len(catalog.tracks),
            len(catalog.users),
            reused,
            started.input_fingerprint,
            content_sha,
        )
    except Exception as error:
        database.fail(started, type(error).__name__)
        raise ImportFailed(started.run_id, type(error).__name__) from None


def load_snapshot(engine, storage_root: Path, snapshot_id: UUID) -> Catalog:
    with engine.connect() as c:
        snapshot = (
            execute(
                c,
                """SELECT d.key, r.stats FROM metadata_snapshots s
            JOIN datasets d ON d.id = s.dataset_id JOIN import_runs r ON r.id = s.created_by_import_run_id
            WHERE s.id = :s AND s.published_at IS NOT NULL""",
                s=snapshot_id,
            )
            .mappings()
            .one_or_none()
        )
        if snapshot is None:
            raise ValidationError("published snapshot does not exist")
        tracks = execute(
            c,
            '''SELECT t.source_id, m.payload FROM snapshot_tracks st
            JOIN tracks t ON t.id = st.track_id LEFT JOIN track_metadata m ON m.id = st.track_metadata_id
            WHERE st.snapshot_id = :s ORDER BY t.source_id COLLATE "C"''',
            s=snapshot_id,
        ).all()
        users = (
            execute(
                c,
                '''SELECT u.source_id FROM snapshot_users su JOIN users u ON u.id = su.user_id
            WHERE su.snapshot_id = :s ORDER BY u.source_id COLLATE "C"''',
                s=snapshot_id,
            )
            .scalars()
            .all()
        )
        rows = (
            execute(
                c,
                """SELECT sa.role, a.* FROM snapshot_artifacts sa
            JOIN data_artifacts a ON a.id = sa.artifact_id WHERE sa.snapshot_id = :s""",
                s=snapshot_id,
            )
            .mappings()
            .all()
        )
    store = ArtifactStore(storage_root, snapshot["key"])
    catalog = validate_catalog(
        Catalog(
            tuple(TrackRecord(source, payload) for source, payload in tracks),
            tuple(UserRecord(source) for source in users),
            dict(snapshot["stats"]),
        )
    )
    files = {
        row["role"]: Artifact(
            row["storage_key"],
            store.root / row["storage_key"],
            row["format"],
            row["sha256"],
            row["byte_size"],
            row["row_count"],
            row["verified_at"],
        )
        for row in rows
    }
    store.validate_catalog_files(files, catalog)
    return catalog
