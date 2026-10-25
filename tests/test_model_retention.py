import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.models.artifacts import export_model, load_model
from src.infrastructure.models.manifest import ModelWeightsPruned, retention_record
from src.infrastructure.models.retention import (
    inspect_model_evidence,
    prune_weights,
)
from src.infrastructure.recommenders.full_popularity import FullPopularity


def exported(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    learning = tmp_path / "learning.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(user_id="u", item_id="a", play_count=3),
                dict(user_id="u", item_id="b", play_count=1),
                dict(user_id="v", item_id="b", play_count=2),
            ]
        ),
        learning,
    )
    directory = tmp_path / "model"
    checksum = export_model(FullPopularity(learning), directory, version_id="retention-test")
    return directory, checksum


def test_pruning_keeps_manifest_identity_and_analysis_but_rejects_inference(tmp_path):
    directory, checksum = exported(tmp_path)
    before = (directory / "manifest.json").read_bytes()
    manifest, state = inspect_model_evidence(directory, expected_manifest_sha256=checksum)
    assert state == "available"
    record = prune_weights(directory, expected_manifest_sha256=checksum, reason="explicit cleanup")
    assert (
        record["status"] == "pruned"
        and sum(x["bytes"] for x in record["removed_files"].values()) > 0
    )
    assert not list(directory.glob("*.npy"))
    assert (directory / "manifest.json").read_bytes() == before
    assert inspect_model_evidence(directory, expected_manifest_sha256=checksum) == (
        manifest,
        "pruned",
    )
    with pytest.raises(ModelWeightsPruned, match="new output"):
        load_model(directory, expected_manifest_sha256=checksum)
    assert (
        prune_weights(directory, expected_manifest_sha256=checksum, reason="explicit cleanup")
        == record
    )


def test_pruning_refuses_changed_arrays_before_deleting_anything(tmp_path):
    directory, checksum = exported(tmp_path)
    path = directory / "plays.npy"
    path.write_bytes(path.read_bytes() + b"changed")
    before = sorted(p.name for p in directory.iterdir())
    with pytest.raises(ValueError, match="hash"):
        prune_weights(directory, expected_manifest_sha256=checksum, reason="cleanup")
    assert sorted(p.name for p in directory.iterdir()) == before
    with pytest.raises(ValueError, match="manifest hash"):
        prune_weights(directory, expected_manifest_sha256=None, reason="cleanup")


def test_missing_or_forged_pruning_evidence_cannot_hide_corruption(tmp_path):
    directory, checksum = exported(tmp_path)
    (directory / "plays.npy").unlink()
    with pytest.raises(FileNotFoundError):
        inspect_model_evidence(directory, expected_manifest_sha256=checksum)
    other, checksum2 = exported(tmp_path / "other")
    prune_weights(other, expected_manifest_sha256=checksum2, reason="cleanup")
    path = other / "retention.json"
    record = json.loads(path.read_text())
    record["removed_files"]["plays"]["sha256"] = "0" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="evidence changed"):
        inspect_model_evidence(other, expected_manifest_sha256=checksum2)


def test_interrupted_pruning_is_explicit_and_resumes_the_same_operation(tmp_path, monkeypatch):
    from pathlib import Path

    directory, checksum = exported(tmp_path)
    unlink = Path.unlink
    calls = []

    def interrupt(path, *args, **kwargs):
        if path.suffix == ".npy":
            calls.append(path)
            if len(calls) == 2:
                raise OSError("simulated disk error")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError, match="disk error"):
            prune_weights(directory, expected_manifest_sha256=checksum, reason="cleanup")
    with pytest.raises(ValueError, match="incomplete"):
        load_model(directory, expected_manifest_sha256=checksum)
    prune_weights(directory, expected_manifest_sha256=checksum, reason="cleanup")
    assert retention_record(directory)["status"] == "pruned"
    assert file_sha256(directory / "manifest.json") == checksum
