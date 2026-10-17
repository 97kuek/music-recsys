"""保存済みカタログをDataFrameで読み、Notebook用に検証・集計する。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.analysis import tag_rows
from src.ingestion.types import Catalog, TrackRecord, ValidationError

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class SnapshotFrames:
    mode: str
    overview: dict[str, Any]
    tracks: "pd.DataFrame"
    users: "pd.DataFrame"
    database_checks: dict[str, bool] | None = None


def load_fixture(path: Path) -> SnapshotFrames:
    """Git保存の合成カタログを読み、DBで検証済みとは扱わない。"""
    import pandas as pd

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("kind") != "synthetic_catalog_fixture":
        raise ValueError("fixture kind must be synthetic_catalog_fixture")
    return SnapshotFrames(
        "fixture",
        {key: raw[key] for key in ("dataset_key", "version_label", "source_ref", "snapshot_id")},
        pd.DataFrame(raw["tracks"], columns=["internal_id", "source_id", "payload"]),
        pd.DataFrame(raw["users"], columns=["internal_id", "source_id"]),
    )


def load_snapshot_frames(
    engine,
    storage_root: Path,
    snapshot_id: UUID,
    *,
    dataset_key: str,
    version_label: str,
) -> SnapshotFrames:
    """公開済みsnapshotを固定し、読み取り専用SQL結果をDataFrameへ読む。

    snapshotに結び付く不変のdataset_version_idを保持し、期待するデータセットと
    配布ラベルも照合する。既存load_snapshotで保存済みParquetのhash・内容を検証する。
    """
    import pandas as pd
    from sqlalchemy import text

    from src.ingestion.pipeline import load_snapshot

    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            metadata = (
                connection.execute(
                    text("""SELECT d.key AS dataset_key, v.version_label, v.source_ref,
                    s.id AS snapshot_id, s.dataset_id, s.dataset_version_id,
                    s.published_at IS NOT NULL AS snapshot_published,
                    r.dependency_lock_sha256,
                    (v.dataset_id = s.dataset_id AND r.dataset_id = s.dataset_id
                     AND r.dataset_version_id = s.dataset_version_id
                     AND r.result_snapshot_id = s.id AND r.status = 'succeeded') AS references_valid,
                    s.manifest = recsys_snapshot_manifest(s.id) AS manifest_matches,
                    s.input_fingerprint = recsys_json_sha256(
                        recsys_input_document(s.created_by_import_run_id)) AS input_fingerprint_matches,
                    s.content_fingerprint = recsys_json_sha256(s.manifest - 'input') AS content_fingerprint_matches,
                    current_setting('transaction_read_only') = 'on' AS read_only
                    FROM metadata_snapshots s
                    JOIN datasets d ON d.id = s.dataset_id
                    JOIN dataset_versions v ON v.id = s.dataset_version_id
                    JOIN import_runs r ON r.id = s.created_by_import_run_id
                    WHERE s.id = :snapshot_id AND s.published_at IS NOT NULL"""),
                    {"snapshot_id": snapshot_id},
                )
                .mappings()
                .one_or_none()
            )
            if metadata is None:
                raise ValidationError("published snapshot does not exist")
            if metadata["dataset_key"] != dataset_key or metadata["version_label"] != version_label:
                raise ValidationError("snapshot dataset/version mismatch")
            params = {"snapshot_id": snapshot_id, "dataset_id": metadata["dataset_id"]}
            tracks = pd.read_sql_query(
                text("""SELECT st.track_id AS internal_id, t.source_id, m.payload,
                    st.track_metadata_id AS metadata_id,
                    (t.id IS NOT NULL AND st.dataset_id = :dataset_id AND t.dataset_id = :dataset_id
                     AND (st.track_metadata_id IS NULL OR
                       (m.id IS NOT NULL AND m.dataset_id = :dataset_id AND m.track_id = st.track_id)))
                     AS reference_valid
                    FROM snapshot_tracks st LEFT JOIN tracks t ON t.id = st.track_id
                    LEFT JOIN track_metadata m ON m.id = st.track_metadata_id
                    WHERE st.snapshot_id = :snapshot_id ORDER BY t.source_id COLLATE "C"
                    """),
                connection,
                params=params,
            )
            users = pd.read_sql_query(
                text("""SELECT su.user_id AS internal_id, u.source_id,
                    (u.id IS NOT NULL AND su.dataset_id = :dataset_id AND u.dataset_id = :dataset_id)
                     AS reference_valid
                    FROM snapshot_users su LEFT JOIN users u ON u.id = su.user_id
                    WHERE su.snapshot_id = :snapshot_id ORDER BY u.source_id COLLATE "C"
                    """),
                connection,
                params=params,
            )
    checks = {
        key: bool(metadata[key])
        for key in (
            "snapshot_published",
            "references_valid",
            "manifest_matches",
            "input_fingerprint_matches",
            "content_fingerprint_matches",
        )
    }
    checks["references_valid"] = (
        checks["references_valid"]
        and bool(tracks["reference_valid"].all())
        and bool(users["reference_valid"].all())
    )
    checks["dataset_version_matches"] = True
    if not all(checks.values()):
        raise ValidationError("snapshot database validation failed")
    catalog = load_snapshot(engine, storage_root, snapshot_id)
    if [(row.source_id, row.payload) for row in tracks.itertuples()] != [
        (track.source_id, track.metadata) for track in catalog.tracks
    ] or users["source_id"].tolist() != [user.source_id for user in catalog.users]:
        raise ValidationError("snapshot SQL/Parquet contents differ")
    checks["parquet_verified"] = True
    overview = {
        key: str(metadata[key])
        for key in (
            "dataset_key",
            "version_label",
            "source_ref",
            "snapshot_id",
            "dataset_version_id",
            "dependency_lock_sha256",
        )
    }
    overview.update(
        {"n_tracks": len(tracks), "n_users": len(users), "read_only": bool(metadata["read_only"])}
    )
    return SnapshotFrames("postgresql", overview, tracks, users, checks)


def validation_rows(
    snapshot: SnapshotFrames,
    *,
    expected_tracks: int | None = None,
    expected_users: int | None = None,
) -> list[dict[str, Any]]:
    """件数・欠損・IDの一意性と読込時のDB/Parquet検証を表示用にまとめる。"""
    rows = []

    def check(name: str, actual: Any, expected: Any = None, passed: bool | None = None) -> None:
        rows.append({"check": name, "actual": actual, "expected": expected, "passed": passed})

    for name, frame, expected_count in (
        ("tracks", snapshot.tracks, expected_tracks),
        ("users", snapshot.users, expected_users),
    ):
        check(
            f"{name}_count",
            len(frame),
            expected_count,
            len(frame) == expected_count if expected_count is not None else None,
        )
        for column in ("source_id", "internal_id"):
            missing = int(frame[column].isna().sum()) + int(
                frame[column].dropna().map(lambda value: not str(value).strip()).sum()
            )
            duplicates = int(frame[column].dropna().duplicated().sum())
            check(f"{name}_{column}_missing", missing, 0, missing == 0)
            check(f"{name}_{column}_duplicates", duplicates, 0, duplicates == 0)
        invalid_uuids = 0
        for value in frame["internal_id"].dropna():
            try:
                UUID(str(value))
            except ValueError:
                invalid_uuids += 1
        check(f"{name}_internal_id_invalid_uuid", invalid_uuids, 0, invalid_uuids == 0)
    payloads = snapshot.tracks["payload"]
    check("tracks_missing_tags", int(payloads.isna().sum()))
    check(
        "tracks_empty_tags",
        sum(payload == {"tags": {}} for payload in payloads if isinstance(payload, dict)),
    )
    for name in (
        "snapshot_published",
        "dataset_version_matches",
        "references_valid",
        "manifest_matches",
        "input_fingerprint_matches",
        "content_fingerprint_matches",
        "parquet_verified",
    ):
        result = (
            snapshot.database_checks.get(name) if snapshot.database_checks is not None else None
        )
        check(name, result if result is not None else "not checked: fixture mode", True, result)
    return rows


def snapshot_eda(snapshot: SnapshotFrames) -> dict[str, Any]:
    """曲タグの出現率・個数・重みを集計する。ユーザー属性は推定しない。"""
    import pandas as pd

    missing, empty, present = 0, 0, 0
    counts, weights = [], []
    for payload in snapshot.tracks["payload"]:
        if payload is None:
            missing += 1
            continue
        tags = payload["tags"]
        if tags:
            present += 1
        else:
            empty += 1
        counts.append(len(tags))
        weights.extend(tags.values())
    catalog = Catalog(
        tuple(TrackRecord(row.source_id, row.payload) for row in snapshot.tracks.itertuples()),
        (),
        {},
    )
    tags = pd.DataFrame(tag_rows(catalog), columns=["tag", "n_tracks", "mean_weight"])
    tags["track_fraction"] = (
        tags["n_tracks"] / len(snapshot.tracks) if len(snapshot.tracks) else 0.0
    )
    return {
        "tag_coverage": pd.DataFrame(
            [
                {"status": "missing", "n_tracks": missing},
                {"status": "empty", "n_tracks": empty},
                {"status": "present", "n_tracks": present},
            ]
        ),
        "tags": tags,
        "tag_counts": counts,
        "tag_weights": weights,
    }
