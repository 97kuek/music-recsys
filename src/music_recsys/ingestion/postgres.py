"""不変台帳の再利用と、run・snapshotのトランザクションを管理する。"""

import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import URL, create_engine, text

from music_recsys.ingestion.types import (
    Artifact,
    Catalog,
    DatasetRelease,
    ValidationError,
    canonical_json,
    fingerprint,
)


def engine_from_environment():
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    if missing := [name for name in required if not os.environ.get(name)]:
        raise ValidationError("missing database environment variables: " + ", ".join(missing))
    try:
        port = int(os.environ.get("POSTGRES_PORT", "5432"))
    except ValueError:
        raise ValidationError("invalid POSTGRES_PORT") from None
    if not 1 <= port <= 65535:
        raise ValidationError("invalid POSTGRES_PORT")
    return create_engine(
        URL.create(
            "postgresql+psycopg",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            database=os.environ["POSTGRES_DB"],
            host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            port=port,
        ),
        hide_parameters=True,
        isolation_level="READ COMMITTED",
    )


def execute(connection, sql, **params):
    return connection.execute(text(sql), params)


def upsert(connection, insert_sql, select_sql, params, expected):
    row = (
        execute(connection, insert_sql + " ON CONFLICT DO NOTHING RETURNING *", **params)
        .mappings()
        .one_or_none()
    )
    if row is None:
        row = execute(connection, select_sql, **params).mappings().one_or_none()
    if row is None or any(row[k] != value for k, value in expected.items()):
        raise ValidationError("immutable registry conflicts with the provided content")
    return row["id"]


def register_artifact(c, dataset_id, a: Artifact):
    params = {
        "d": dataset_id,
        "format": a.format,
        "key": a.storage_key,
        "sha": a.sha256,
        "bytes": a.byte_size,
        "rows": a.row_count,
        "verified": a.verified_at,
    }
    return upsert(
        c,
        """INSERT INTO data_artifacts
        (dataset_id, format, storage_key, sha256, byte_size, row_count, verified_at)
        VALUES (:d, :format, :key, :sha, :bytes, :rows, :verified)""",
        "SELECT * FROM data_artifacts WHERE dataset_id = :d AND sha256 = :sha",
        params,
        {
            "format": a.format,
            "storage_key": a.storage_key,
            "byte_size": a.byte_size,
            "row_count": a.row_count,
        },
    )


@dataclass(frozen=True)
class StartedImport:
    dataset_id: UUID
    version_id: UUID
    run_id: UUID
    input_fingerprint: str
    input_document: dict


class PostgresStore:
    def __init__(self, engine):
        self.engine = engine

    def revisions(self) -> list[str]:
        with self.engine.connect() as c:
            return sorted(execute(c, "SELECT version_num FROM alembic_version").scalars())

    def start(
        self,
        release: DatasetRelease,
        sources: dict[str, Artifact],
        extras: tuple[Artifact, ...],
        config: dict,
        adapter_name: str,
        adapter_version: str,
        pipeline_version: str,
        lock_sha256: str,
    ) -> StartedImport:
        manifest_sha = fingerprint(release.manifest)
        document = {
            "dataset": release.key,
            "dataset_version": {"source_ref": release.source_ref, "manifest_sha256": manifest_sha},
            "files": [{"name": name, "sha256": sources[name].sha256} for name in sorted(sources)],
            "adapter": {"name": adapter_name, "version": adapter_version},
            "pipeline_version": pipeline_version,
            "dependency_lock_sha256": lock_sha256,
            "config": config,
        }
        input_sha = fingerprint(document)
        with self.engine.begin() as c:
            d = upsert(
                c,
                "INSERT INTO datasets (key) VALUES (:key)",
                "SELECT * FROM datasets WHERE key = :key",
                {"key": release.key},
                {"key": release.key},
            )
            v = upsert(
                c,
                """INSERT INTO dataset_versions
                (dataset_id, version_label, source_ref, source_manifest, manifest_sha256)
                VALUES (:d, :label, :ref, CAST(:manifest AS jsonb), :sha)""",
                "SELECT * FROM dataset_versions WHERE dataset_id = :d AND source_ref = :ref AND manifest_sha256 = :sha",
                {
                    "d": d,
                    "label": release.version_label,
                    "ref": release.source_ref,
                    "manifest": canonical_json(release.manifest),
                    "sha": manifest_sha,
                },
                {"version_label": release.version_label, "source_manifest": release.manifest},
            )
            source_ids = []
            for spec in release.sources:
                a = register_artifact(c, d, sources[spec.name])
                source_ids.append(
                    upsert(
                        c,
                        """INSERT INTO source_files
                    (dataset_id, dataset_version_id, logical_name, source_url,
                     expected_checksum_algorithm, expected_checksum, artifact_id)
                    VALUES (:d, :v, :name, :url, :algorithm, :checksum, :a)""",
                        "SELECT * FROM source_files WHERE dataset_version_id = :v AND logical_name = :name",
                        {
                            "d": d,
                            "v": v,
                            "name": spec.name,
                            "url": spec.source_url,
                            "algorithm": spec.checksum_algorithm,
                            "checksum": spec.checksum,
                            "a": a,
                        },
                        {
                            "artifact_id": a,
                            "source_url": spec.source_url,
                            "expected_checksum_algorithm": spec.checksum_algorithm,
                            "expected_checksum": spec.checksum,
                        },
                    )
                )
            for a in extras:
                register_artifact(c, d, a)
            r = execute(
                c,
                """INSERT INTO import_runs
                (dataset_id, dataset_version_id, adapter_name, adapter_version, pipeline_version,
                 dependency_lock_sha256, config, input_fingerprint)
                VALUES (:d, :v, :name, :adapter, :pipeline, :lock, CAST(:config AS jsonb), :sha) RETURNING id""",
                d=d,
                v=v,
                name=adapter_name,
                adapter=adapter_version,
                pipeline=pipeline_version,
                lock=lock_sha256,
                config=canonical_json(config),
                sha=input_sha,
            ).scalar_one()
            for s in source_ids:
                execute(
                    c,
                    """INSERT INTO import_run_sources
                    (dataset_id, dataset_version_id, import_run_id, source_file_id)
                    VALUES (:d, :v, :r, :s)""",
                    d=d,
                    v=v,
                    r=r,
                    s=s,
                )
            execute(
                c, "UPDATE import_runs SET input_sealed_at = clock_timestamp() WHERE id = :r", r=r
            )
        return StartedImport(d, v, r, input_sha, document)

    def fail(self, run: StartedImport, error_type: str) -> None:
        with self.engine.begin() as c:
            execute(
                c,
                """UPDATE import_runs SET status = 'failed', error_type = :error,
                finished_at = clock_timestamp() WHERE id = :r""",
                r=run.run_id,
                error=error_type,
            )

    def publish(self, run: StartedImport, catalog: Catalog, artifacts: dict[str, Artifact]):
        manifest = {
            "input": run.input_document,
            "tracks": [
                {
                    "source_id": t.source_id,
                    "metadata_sha256": fingerprint(t.metadata) if t.metadata is not None else None,
                }
                for t in catalog.tracks
            ],
            "users": [u.source_id for u in catalog.users],
            "artifacts": {
                role: {"sha256": a.sha256, "row_count": a.row_count}
                for role, a in artifacts.items()
            },
        }
        content_sha = fingerprint({k: v for k, v in manifest.items() if k != "input"})
        with self.engine.begin() as c:
            execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))",
                key=str(run.version_id) + ":" + run.input_fingerprint,
            )
            existing = (
                execute(
                    c,
                    """SELECT * FROM metadata_snapshots
                WHERE dataset_version_id = :v AND input_fingerprint = :sha""",
                    v=run.version_id,
                    sha=run.input_fingerprint,
                )
                .mappings()
                .one_or_none()
            )
            reused = existing is not None
            if reused:
                if (
                    existing["content_fingerprint"] != content_sha
                    or existing["manifest"] != manifest
                ):
                    raise ValidationError(
                        "same input produced a different catalog or Parquet artifact"
                    )
                snapshot_id = existing["id"]
            else:
                track_ids, user_ids = [], []
                for t in catalog.tracks:
                    track_id = upsert(
                        c,
                        "INSERT INTO tracks (dataset_id, source_id) VALUES (:d, :source)",
                        "SELECT * FROM tracks WHERE dataset_id = :d AND source_id = :source",
                        {"d": run.dataset_id, "source": t.source_id},
                        {"source_id": t.source_id},
                    )
                    m = None
                    if t.metadata is not None:
                        sha = fingerprint(t.metadata)
                        m = upsert(
                            c,
                            """INSERT INTO track_metadata
                            (dataset_id, track_id, payload, content_sha256)
                            VALUES (:d, :t, CAST(:payload AS jsonb), :sha)""",
                            "SELECT * FROM track_metadata WHERE track_id = :t AND content_sha256 = :sha",
                            {
                                "d": run.dataset_id,
                                "t": track_id,
                                "payload": canonical_json(t.metadata),
                                "sha": sha,
                            },
                            {"payload": t.metadata, "content_sha256": sha},
                        )
                    track_ids.append((track_id, m))
                for u in catalog.users:
                    user_ids.append(
                        upsert(
                            c,
                            "INSERT INTO users (dataset_id, source_id) VALUES (:d, :source)",
                            "SELECT * FROM users WHERE dataset_id = :d AND source_id = :source",
                            {"d": run.dataset_id, "source": u.source_id},
                            {"source_id": u.source_id},
                        )
                    )
                artifact_ids = {
                    role: register_artifact(c, run.dataset_id, a) for role, a in artifacts.items()
                }
                snapshot_id = execute(
                    c,
                    """INSERT INTO metadata_snapshots
                    (dataset_id, dataset_version_id, created_by_import_run_id, input_fingerprint,
                     content_fingerprint, manifest)
                    VALUES (:d, :v, :r, :sha, :content, CAST(:manifest AS jsonb)) RETURNING id""",
                    d=run.dataset_id,
                    v=run.version_id,
                    r=run.run_id,
                    sha=run.input_fingerprint,
                    content=content_sha,
                    manifest=canonical_json(manifest),
                ).scalar_one()
                for t, m in track_ids:
                    execute(
                        c,
                        """INSERT INTO snapshot_tracks
                        (dataset_id, snapshot_id, track_id, track_metadata_id) VALUES (:d, :s, :t, :m)""",
                        d=run.dataset_id,
                        s=snapshot_id,
                        t=t,
                        m=m,
                    )
                for u in user_ids:
                    execute(
                        c,
                        "INSERT INTO snapshot_users (dataset_id, snapshot_id, user_id) VALUES (:d, :s, :u)",
                        d=run.dataset_id,
                        s=snapshot_id,
                        u=u,
                    )
                for role, a in artifact_ids.items():
                    execute(
                        c,
                        """INSERT INTO snapshot_artifacts
                        (dataset_id, snapshot_id, role, artifact_id) VALUES (:d, :s, :role, :a)""",
                        d=run.dataset_id,
                        s=snapshot_id,
                        role=role,
                        a=a,
                    )
            stats = {**catalog.stats, "tracks": len(catalog.tracks), "users": len(catalog.users)}
            execute(
                c,
                """UPDATE import_runs SET status = 'succeeded', result_snapshot_id = :s,
                finished_at = clock_timestamp(), stats = CAST(:stats AS jsonb) WHERE id = :r""",
                s=snapshot_id,
                r=run.run_id,
                stats=canonical_json(stats),
            )
            if not reused:
                execute(
                    c,
                    "UPDATE metadata_snapshots SET published_at = clock_timestamp() WHERE id = :s",
                    s=snapshot_id,
                )
        return snapshot_id, reused, content_sha
