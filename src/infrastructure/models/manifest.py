"""Model manifest identity and retained-weight availability checks."""

import json
from pathlib import Path

from src.domain.recommendation.models import ModelSpec
from src.infrastructure.datasets.listening_data import file_sha256


def read_model_manifest(directory, *, expected_manifest_sha256=None):
    """Validate model identity and provenance without requiring retained weight arrays."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if (
        expected_manifest_sha256 is not None
        and file_sha256(manifest_path) != expected_manifest_sha256
    ):
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["format"] != "music-recsys-model-v1":
        raise ValueError("unsupported model format")
    spec = ModelSpec(**manifest["spec"])
    provenance = manifest["provenance"]
    if (
        spec.parameters != provenance["parameters"]
        or spec.seed != provenance["seed"]
        or spec.training_ref != provenance["learning_sha256"]
        or spec.implementation_ref != provenance["source_sha256"]
    ):
        raise ValueError("manifest provenance mismatch")
    required = {"items", "plays"} | {
        "popularity": {"scores"},
        "itemcf": {"targets", "weights"},
        "als": {"users", "item_factors", "user_factors"},
        "bpr": {"users", "item_factors", "user_factors", "active_users"},
    }.get(spec.algorithm, set())
    if (
        spec.algorithm not in {"popularity", "itemcf", "als", "bpr"}
        or set(manifest["files"]) != required
    ):
        raise ValueError("invalid model file set")
    return manifest, spec


class ModelWeightsPruned(ValueError):
    """The model is documented but no longer available for inference."""


def retention_record(directory, *, expected_manifest_sha256=None, allow_incomplete=False):
    directory = Path(directory)
    manifest, _ = read_model_manifest(directory, expected_manifest_sha256=expected_manifest_sha256)
    marker = directory / "retention.json"
    if not marker.exists():
        return None
    record = json.loads(marker.read_text())
    if (
        record.get("format") != "music-recsys-model-retention-v1"
        or record.get("status") not in {"pruning", "pruned"}
        or record.get("manifest_sha256") != file_sha256(directory / "manifest.json")
        or set(record.get("removed_files", {})) != set(manifest["files"])
    ):
        raise ValueError("Invalid model retention evidence")
    if record["status"] != "pruned" and not allow_incomplete:
        raise ValueError("Model pruning is incomplete; inspect the retention record")
    for name, metadata in manifest["files"].items():
        removed = record["removed_files"][name]
        path = directory / f"{name}.npy"
        if (
            removed.get("sha256") != metadata["sha256"]
            or type(removed.get("bytes")) is not int
            or removed["bytes"] <= 0
            or path.is_symlink()
        ):
            raise ValueError("Model retention file evidence changed")
        if path.exists():
            if record["status"] == "pruned" or file_sha256(path) != removed["sha256"]:
                raise ValueError("Model retention state differs from its files")
    return record
