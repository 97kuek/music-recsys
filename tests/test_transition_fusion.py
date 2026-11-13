"""Budget-aware fusion diagnostics and train-only transition source integration."""

import json
import os
import runpy
from pathlib import Path

import numpy as np
import pytest

from backend.infrastructure.evaluation.hybrid import PoolRecall


def test_pool_recall_distinguishes_20_union_from_40_source_control(monkeypatch):
    monkeypatch.setattr(np, "asnumpy", np.asarray, raising=False)

    class Writer:
        def add(self, row):
            self.row = row

    writer = Writer()
    recall = PoolRecall({str(i): i for i in range(80)}, [90], writer, top_m=20, retrieval_m=40)
    sources = [np.arange(40)[None, :], np.arange(40, 80)[None, :]]
    pool = np.concatenate([s[:, :20] for s in sources], axis=1)
    observations = [[("u", str(i), 90, 0) for i in (0, 30, 40, 70)]]
    recall.add("new", sources, pool, observations, ["u"], np)
    rows = recall.finish(np, 1)
    assert [r["scope"] for r in rows] == [
        "source_a20",
        "source_b20",
        "union20",
        "source_a40",
        "source_b40",
    ]
    assert writer.row["values"] == [0.25, 0.25, 0.5, 0.5, 0.5]


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test")
def test_transition_fusion_cli_path_without_test_data(tmp_path, monkeypatch):
    from backend.application.hybrid import run_study
    from backend.bootstrap import publish_hybrid_report
    from backend.infrastructure.research.configuration import load_settings
    from backend.infrastructure.research.hybrid import HybridStudy
    from backend.infrastructure.research.search import load_study_config
    from backend.interfaces.cli.evaluate import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/hybrid/transition-ease.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "MUST_NOT_READ_TEST"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "fusion"),
    )
    for model, config in [
        ("transition_rules", "configs/transition_rules/first-listen.json"),
        ("ease", "configs/ease/base.json"),
    ]:
        source = load_settings(config)
        source["experiment"].update(raw["experiment"], name=model, output=str(tmp_path / model))
        if model == "ease":
            source["model"]["parameters"].update(
                regularization=10, block_size=2, memory_budget_mb=4096
            )
        else:
            source["model"]["parameters"]["input_memory_mb"] = 128
        path = tmp_path / (model + ".json")
        path.write_text(json.dumps(source), encoding="utf-8")
        run(path)
        raw["pipeline"]["sources"][model]["validation"] = str(tmp_path / model)
    path = tmp_path / "fusion.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    study = HybridStudy(load_study_config(path), report=publish_hybrid_report)
    assert study.config.metric is study.config.task is None
    study.freeze()
    prepare_original = study.prepare

    def prepare(name):
        study.build_source(name)
        return prepare_original(name)

    monkeypatch.setattr(study, "prepare", prepare)
    run_study(tuple(study.sources), study.conditions, study)
    result = json.loads((study.output / "result.json").read_text(encoding="utf-8"))
    assert len(result["groups"]) == 3
    assert result["population"]["users"] == 4
    assert result["selection"] == "not_selected"
    assert result["figures"]
    fused = json.loads(
        (study.output / "transition_rules-ease-rrf/result.json").read_text(encoding="utf-8")
    )
    assert {r["scope"] for r in fused["pool_recall"]} == {
        "source_a20",
        "source_b20",
        "union20",
        "source_a40",
        "source_b40",
    }
    assert {r["task"] for r in fused["diversity"]} == {"new", "repeat"}
