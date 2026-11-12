import json
import os
import runpy
import shutil
from functools import wraps
from pathlib import Path

import pytest


def test_invalid_phase_is_rejected_before_reading_any_input():
    from backend.infrastructure.research.hybrid import HybridStudy

    with pytest.raises(ValueError, match="phase"):
        HybridStudy(None, phase="unknown")


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test")
def test_final_hybrid_refits_test_learning_and_preserves_validation(tmp_path, monkeypatch):
    from backend.application.hybrid import run_study
    from backend.bootstrap import publish_hybrid_report as publish
    from backend.infrastructure.datasets.listening_data import file_sha256
    from backend.infrastructure.recommenders.full_bpr import FullBPR
    from backend.infrastructure.research.hybrid import HybridStudy
    from backend.infrastructure.research.search import load_study_config

    setup = runpy.run_path(str(Path(__file__).with_name("test_hybrid.py")))[
        "test_full_hybrid_validation_pairs_seeds_keeps_sources_and_never_reads_test"
    ]
    setup(tmp_path, monkeypatch)
    config = load_study_config(tmp_path / "hybrid.json")
    validation = {p: file_sha256(p) for p in config.output.rglob("*") if p.is_file()}
    # The validation fixture proves this path was never opened. Publish the held-out fixture now.
    shutil.copytree(tmp_path / "bundle/test", config.test_dataset)
    trained = []
    original_init = FullBPR.__init__

    @wraps(original_init)
    def record_training(self, learning_path, **kwargs):
        trained.append((Path(learning_path), kwargs["seed"]))
        return original_init(self, learning_path, **kwargs)

    monkeypatch.setattr(FullBPR, "__init__", record_training)
    study = HybridStudy(config, phase="test", report=publish, ease_memory_budget_mb=4096)
    frozen = study.freeze()
    assert frozen["phase"] == "test" and frozen["selection"] == "not_selected"
    assert frozen["execution_options"]["ease_memory_budget_mb"] == 4096
    assert len(frozen["conditions"]) == 18
    prepare = study.prepare

    def build(name):
        study.build_source(name)
        return prepare(name)

    monkeypatch.setattr(study, "prepare", build)
    output = Path(run_study(tuple(study.sources), study.conditions, study))
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["phase"] == "test" and len(result["groups"]) == 10
    assert result["population"]["users"] == 4
    assert result["selection"] == "not_selected"
    assert {seed for _, seed in trained} == {42, 43, 44}
    assert all(p == study.paths["learning"] for p, _ in trained)
    assert all(file_sha256(p) == h for p, h in validation.items())
    assert {r["phase"] for r in result["observations"]} == {"test"}
    for source in result["sources"]:
        r = json.loads((Path(source["directory"]) / "result.json").read_text(encoding="utf-8"))
        assert r["phase"] == "test" and r["primary"]["ks"] == list(range(2, 21))
        assert {v["task"] for v in r["diversity"]} == {"new", "repeat"}
    with pytest.raises(FileExistsError):
        study.freeze()
