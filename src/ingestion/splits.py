"""検証済みの分割成果物を公開し、snapshotに固定された不変台帳から読み戻す。"""

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import Engine

from src.ingestion.artifacts import ArtifactStore
from src.ingestion.postgres import execute, register_artifact, upsert
from src.ingestion.split_validation import validate_saved_partitions, validate_summary
from src.ingestion.types import Artifact, ValidationError, canonical_json, fingerprint

REQUIRED_ROLES = ("events", "train", "validation", "test")
OPTIONAL_ROLES = ("validation_benchmark", "test_benchmark")
ALL_ROLES = REQUIRED_ROLES + OPTIONAL_ROLES


def _manifest(artifacts: dict[str, Artifact]) -> dict:
    return {
        role: {
            "sha256": a.sha256,
            "format": a.format,
            "byte_size": a.byte_size,
            "row_count": a.row_count,
        }
        for role, a in artifacts.items()
    }


def _verify(store: ArtifactStore, artifacts: dict[str, Artifact]) -> None:
    if not set(REQUIRED_ROLES) <= artifacts.keys() or artifacts.keys() - set(ALL_ROLES):
        raise ValidationError("split requires events/train/validation/test and supported roles")
    for role, a in artifacts.items():
        expected_format = "parquet" if role in REQUIRED_ROLES else "json"
        if a.format != expected_format:
            raise ValidationError("split artifact format mismatch")
        store.verify(a)
        if a.format == "json":
            try:
                canonical_json(json.loads(a.path.read_text(encoding="utf-8")))
            except (ValueError, UnicodeError):
                raise ValidationError("invalid benchmark JSON artifact") from None


def _verify_history(import_config: dict, events: Artifact) -> None:
    history = import_config.get("history_events")
    if not isinstance(history, dict) or any(
        history.get(key) != getattr(events, key) for key in ("sha256", "row_count", "storage_key")
    ):
        raise ValidationError("events do not match snapshot playback history provenance")


def _identity(snapshot_id: UUID, config: dict, manifest: dict, summary: dict) -> str:
    return fingerprint(
        {
            "snapshot_id": str(snapshot_id),
            "config": config,
            "artifacts": manifest,
            "summary": summary,
        }
    )


def publish_split(
    engine: Engine,
    storage_root: Path,
    snapshot_id: UUID,
    config: dict,
    artifacts: dict[str, Artifact],
    summary: dict,
) -> UUID:
    """全ファイルを照合してから1トランザクションで公開し、同一内容は再利用する。"""
    if not isinstance(config, dict) or not isinstance(summary, dict):
        raise ValidationError("split config and summary must be JSON objects")
    # 呼出し側の可変dictを公開処理中に変更されないようJSONで確定する。
    config = json.loads(canonical_json(config))
    summary = json.loads(canonical_json(summary))
    artifacts = dict(artifacts)
    with engine.connect() as c:
        snapshot = (
            execute(
                c,
                """SELECT s.dataset_id, s.dataset_version_id, d.key, r.config AS import_config
            FROM metadata_snapshots s
            JOIN datasets d ON d.id = s.dataset_id
            JOIN import_runs r ON r.id = s.created_by_import_run_id
            WHERE s.id = :s AND s.published_at IS NOT NULL""",
                s=snapshot_id,
            )
            .mappings()
            .one_or_none()
        )
    if snapshot is None:
        raise ValidationError("split requires a published snapshot")
    _verify(ArtifactStore(storage_root, snapshot["key"]), artifacts)
    _verify_history(snapshot["import_config"], artifacts["events"])
    validate_saved_partitions({role: a.path for role, a in artifacts.items()}, config, summary)
    manifest = _manifest(artifacts)
    content_sha = _identity(snapshot_id, config, manifest, summary)
    with engine.begin() as c:
        execute(c, "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))", key=content_sha)
        artifact_ids = {
            role + "_artifact_id": register_artifact(c, snapshot["dataset_id"], a)
            for role, a in artifacts.items()
        }
        params = {
            "dataset_id": snapshot["dataset_id"],
            "dataset_version_id": snapshot["dataset_version_id"],
            "snapshot_id": snapshot_id,
            "config": canonical_json(config),
            "summary": canonical_json(summary),
            "artifact_manifest": canonical_json(manifest),
            "content_fingerprint": content_sha,
            **{
                role + "_artifact_id": artifact_ids.get(role + "_artifact_id") for role in ALL_ROLES
            },
        }
        columns = ", ".join(params)
        values = ", ".join(
            f"CAST(:{key} AS jsonb)"
            if key in ("config", "summary", "artifact_manifest")
            else f":{key}"
            for key in params
        )
        return upsert(
            c,
            f"INSERT INTO benchmark_splits ({columns}) VALUES ({values})",
            "SELECT * FROM benchmark_splits WHERE content_fingerprint = :content_fingerprint",
            params,
            {
                **{
                    key: value
                    for key, value in params.items()
                    if key not in ("config", "summary", "artifact_manifest")
                },
                "config": config,
                "summary": summary,
                "artifact_manifest": manifest,
            },
        )


def load_split(engine: Engine, storage_root: Path, split_id: UUID) -> dict:
    """台帳のhashと保存物を検証し、同じsnapshot・設定・成果物を返す。"""
    with engine.connect() as c:
        row = (
            execute(
                c,
                """SELECT b.*, d.key AS dataset_key, v.version_label, r.config AS import_config
            FROM benchmark_splits b
            JOIN datasets d ON d.id = b.dataset_id
            JOIN dataset_versions v ON v.id = b.dataset_version_id
            JOIN metadata_snapshots s ON s.id = b.snapshot_id
            JOIN import_runs r ON r.id = s.created_by_import_run_id
            WHERE b.id = :b AND s.published_at IS NOT NULL""",
                b=split_id,
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValidationError("published split not found")
        artifacts = {}
        root = Path(storage_root).resolve()
        for role in ALL_ROLES:
            artifact_id = row[role + "_artifact_id"]
            if artifact_id is None:
                continue
            a = (
                execute(
                    c,
                    "SELECT * FROM data_artifacts WHERE id = :a AND dataset_id = :d",
                    a=artifact_id,
                    d=row["dataset_id"],
                )
                .mappings()
                .one()
            )
            artifacts[role] = Artifact(
                a["storage_key"],
                root / a["storage_key"],
                a["format"],
                a["sha256"],
                a["byte_size"],
                a["row_count"],
                a["verified_at"],
            )
    manifest = _manifest(artifacts)
    if (
        manifest != row["artifact_manifest"]
        or _identity(row["snapshot_id"], row["config"], manifest, row["summary"])
        != row["content_fingerprint"]
    ):
        raise ValidationError("split registry content fingerprint mismatch")
    _verify(ArtifactStore(root, row["dataset_key"]), artifacts)
    _verify_history(row["import_config"], artifacts["events"])
    validate_summary(
        row["config"], row["summary"], {role: artifacts[role].row_count for role in REQUIRED_ROLES}
    )
    return {
        **{
            key: row[key]
            for key in (
                "id",
                "snapshot_id",
                "dataset_key",
                "version_label",
                "dataset_version_id",
                "config",
                "summary",
            )
        },
        "artifacts": artifacts,
    }
