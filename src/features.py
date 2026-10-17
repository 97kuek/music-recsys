"""同じSnapshotの非負特徴を、モデル・評価・Notebookへ共有する。"""

import argparse
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID

from src.ingestion.types import canonical_json
from src.types import validate_id

FeatureMap = dict[str, dict[str, float]]
FEATURE_MODELS = frozenset({"content", "hybrid"})


def validate_features(features: object) -> FeatureMap:
    if not isinstance(features, dict):
        raise ValueError("features must be an item / vector mapping")
    for item, vector in features.items():
        validate_id(item)
        if not isinstance(vector, dict):
            raise ValueError("特徴は非負の有限値の辞書が必要です")
        for name, value in vector.items():
            validate_id(name)
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("特徴は非負の有限値の辞書が必要です")
    return features


def unit_vector(vector: dict) -> dict:
    """先に最大値で縮尺を揃え、有限の大きな非負特徴でもL2正規化する。"""
    scale = max(vector.values(), default=0)
    if not scale:
        return {}
    scaled = {k: v / scale for k, v in sorted(vector.items()) if v > 0}
    norm = math.hypot(*scaled.values())
    return {k: v / norm for k, v in scaled.items()}


def _checksum(raw: dict) -> str:
    return hashlib.sha256(canonical_json(raw).encode()).hexdigest()


def load_features(path: Path, *, dataset_id: str, snapshot_id: str | None) -> FeatureMap:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("dataset_id") != dataset_id
        or raw.get("snapshot_id") != snapshot_id
    ):
        raise ValueError("比較条件不一致: featuresの出典")
    if any(key in raw for key in ("source", "transformation")) and "content_sha256" not in raw:
        raise ValueError("features checksum is required for exported artifacts")
    if "content_sha256" in raw:
        body = {k: v for k, v in raw.items() if k != "content_sha256"}
        if _checksum(body) != raw["content_sha256"]:
            raise ValueError("features checksum mismatch")
    return validate_features(raw["features"])


def write_features(
    output: Path, *, dataset_id: str, snapshot_id: str, features: FeatureMap, source: dict
) -> dict:
    validate_id(dataset_id)
    validate_id(snapshot_id)
    validate_features(features)
    raw = {
        "dataset_id": dataset_id,
        "snapshot_id": snapshot_id,
        "features": features,
        "source": source,
        "transformation": "snapshot-weighted-tags-v1",
    }
    raw["content_sha256"] = _checksum(raw)
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    return raw


def export_snapshot_features(engine, storage_root: Path, snapshot_id: UUID, output: Path) -> dict:
    from sqlalchemy import text

    from src.ingestion.pipeline import load_snapshot

    catalog = load_snapshot(engine, storage_root, snapshot_id)
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("""SELECT d.key, s.content_fingerprint, a.sha256
            FROM metadata_snapshots s JOIN datasets d ON d.id=s.dataset_id
            JOIN snapshot_artifacts sa ON sa.snapshot_id=s.id AND sa.role='tracks'
            JOIN data_artifacts a ON a.id=sa.artifact_id WHERE s.id=:id AND s.published_at IS NOT NULL"""),
                {"id": snapshot_id},
            )
            .mappings()
            .one()
        )
    features = {
        t.source_id: t.metadata["tags"]
        for t in catalog.tracks
        if t.metadata and t.metadata.get("tags")
    }
    return write_features(
        output,
        dataset_id=row["key"],
        snapshot_id=str(snapshot_id),
        features=features,
        source={
            "metadata_sha256": row["sha256"],
            "snapshot_content_fingerprint": row["content_fingerprint"],
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage-root", type=Path, default=Path("data/imports"))
    args = parser.parse_args()
    engine = None
    try:
        from src.ingestion.postgres import engine_from_environment

        engine = engine_from_environment()
        raw = export_snapshot_features(engine, args.storage_root, args.snapshot_id, args.output)
        print(
            json.dumps(
                {
                    "status": "exported",
                    "feature_items": len(raw["features"]),
                    "sha256": raw["content_sha256"],
                }
            )
        )
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
