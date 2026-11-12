import json
from dataclasses import asdict

import pytest

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.research.search import load_study_config
from backend.infrastructure.research.store import atomic_json


def test_hybrid_test_reader_requires_explicit_phase_and_retains_validation_evidence(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from backend.infrastructure.research import results
    from backend.infrastructure.research.hybrid_config import reference

    config = replace(
        load_study_config("configs/hybrid/base.json"),
        output=tmp_path / "validation",
        features=tmp_path / "features.json",
    )
    config.features.write_text("{}")
    config.output.mkdir()
    atomic_json(config.output / "config.json", {"sentinel": "validation"})
    atomic_json(config.output / "result.json", {"sentinel": "validation"})
    test = replace(config, output=tmp_path / "test")
    saved = dict(
        settings=json.loads(json.dumps(asdict(test), default=str)),
        manifest={"phase": "test"},
        features_sha256=file_sha256(config.features),
        validation_reference=reference(config.output),
    )
    atomic_json(test.output / "config.json", saved)
    atomic_json(test.output / "result.json", dict(phase="test", status="succeeded"))
    monkeypatch.setattr(results, "matches_plan", lambda config, saved: True)
    monkeypatch.setattr(results, "read_bundle", lambda path: ({"phase": "test"}, {}))
    assert results.read_result(config, phase="test")["phase"] == "test"
    with pytest.raises(KeyError):
        results.read_result(config)
    atomic_json(test.output / "result.json", dict(phase="validation", status="succeeded"))
    with pytest.raises(ValueError, match="phase"):
        results.read_result(config, phase="test")
    atomic_json(test.output / "result.json", dict(phase="test", status="succeeded"))
    atomic_json(config.output / "result.json", {"changed": True})
    with pytest.raises(ValueError, match="evidence changed"):
        results.read_result(config, phase="test")
