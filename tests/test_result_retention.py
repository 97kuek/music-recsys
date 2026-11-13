import json

import pytest

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.research.results import verify_artifacts


def test_retired_detail_requires_regeneration_without_rewriting_aggregate(tmp_path):
    detail = tmp_path / "scores.parquet"
    detail.write_bytes(b"original individual evidence")
    artifacts = {"scores": {"path": detail.name, "sha256": file_sha256(detail)}}
    aggregate = tmp_path / "result.json"
    aggregate.write_text(json.dumps({"mean": 0.125, "artifacts": artifacts}))
    original = aggregate.read_bytes()
    verify_artifacts(tmp_path, artifacts)
    (tmp_path / "artifacts.retention.json").write_text(
        json.dumps({"files": [{"path": detail.name, "sha256": artifacts["scores"]["sha256"]}]})
    )
    detail.unlink()
    with pytest.raises(ValueError, match="explicitly retired"):
        verify_artifacts(tmp_path, artifacts)
    assert aggregate.read_bytes() == original
    # A stale retirement record cannot excuse changed regenerated evidence.
    detail.write_bytes(b"different individual evidence")
    with pytest.raises(ValueError, match="checksum"):
        verify_artifacts(tmp_path, artifacts)
