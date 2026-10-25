"""Explicit removal of weight arrays while keeping immutable model/evaluation evidence."""

from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.models.artifacts import load_model
from src.infrastructure.models.manifest import read_model_manifest, retention_record
from src.infrastructure.research.store import atomic_json


def inspect_model_evidence(directory, *, expected_manifest_sha256=None):
    """Verify available arrays, or verified explicit pruning, for read-only analysis."""
    manifest, _ = read_model_manifest(directory, expected_manifest_sha256=expected_manifest_sha256)
    record = retention_record(directory, expected_manifest_sha256=expected_manifest_sha256)
    if record is None:
        load_model(directory, expected_manifest_sha256=expected_manifest_sha256)
    return manifest, "pruned" if record else "available"


def prune_weights(directory, *, expected_manifest_sha256, reason):
    """Record every verified array before deleting; resume only the same explicit operation."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A retention reason is required")
    directory = Path(directory)
    if (
        directory.is_symlink()
        or (directory / "manifest.json").is_symlink()
        or not isinstance(expected_manifest_sha256, str)
        or file_sha256(directory / "manifest.json") != expected_manifest_sha256
    ):
        raise ValueError("An unchanged, non-symlink model manifest hash is required")
    manifest, _ = read_model_manifest(directory, expected_manifest_sha256=expected_manifest_sha256)
    record = retention_record(
        directory, expected_manifest_sha256=expected_manifest_sha256, allow_incomplete=True
    )
    if record is None:
        load_model(directory, expected_manifest_sha256=expected_manifest_sha256)
        removed = {}
        for name, metadata in manifest["files"].items():
            path = directory / f"{name}.npy"
            if path.is_symlink() or file_sha256(path) != metadata["sha256"]:
                raise ValueError("Model changed before pruning")
            removed[name] = dict(sha256=metadata["sha256"], bytes=path.stat().st_size)
        record = dict(
            format="music-recsys-model-retention-v1",
            status="pruning",
            manifest_sha256=expected_manifest_sha256,
            removed_files=removed,
            reason=reason,
            recovery="Metrics CSV cannot restore arrays; retrain to a new output; GPU weights may differ",
        )
        atomic_json(directory / "retention.json", record)
    elif record["reason"] != reason:
        raise ValueError("Retention reason differs from the recorded operation")
    for name in record["removed_files"]:
        path = directory / f"{name}.npy"
        if path.exists():
            if path.is_symlink() or file_sha256(path) != record["removed_files"][name]["sha256"]:
                raise ValueError("Model changed during pruning")
            path.unlink()
    record["status"] = "pruned"
    atomic_json(directory / "retention.json", record)
    retention_record(directory, expected_manifest_sha256=expected_manifest_sha256)
    return record
