import json
import runpy
from pathlib import Path

import pytest


def test_als_grid_end_to_end_and_comparison_guards(tmp_path):
    from music_recsys.als_grid import run_grid
    from music_recsys.experiment_search import load_study_config
    from music_recsys.full_analysis import curves
    from music_recsys.full_evaluation import run

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = json.loads(Path("configs/als.json").read_text())
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "never-open-test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "als"),
    )
    path = tmp_path / "als.json"
    path.write_text(json.dumps(raw))
    baselines = []
    for model in ("popularity", "itemcf"):
        baseline = json.loads(json.dumps(raw))
        baseline["experiment"]["output"] = str(tmp_path / model)
        baseline["model"] = dict(name=model)
        baseline["search"] = dict(stage="A", seeds=[0], max_trials=1)
        target = tmp_path / f"{model}.json"
        target.write_text(json.dumps(baseline))
        run(target)
        baselines.append(target)
    output = run_grid(path, compare=baselines)
    result = json.loads((output / "result.json").read_text())
    assert len(result["sources"]) == 5
    assert result["selection"] == "not_selected"
    assert {r["factors"] for r in result["metrics"] if r["model"] == "als"} == {32, 64, 128}
    assert {"recall.png", "recall_factors.png", "repeat_recall.png", "binary_ndcg.png"} <= set(
        result["figures"]
    )
    assert not any("api_cost" in f for f in result["figures"])
    frame = curves(load_study_config(path))
    assert set(frame.model) == {"als", "popularity", "itemcf"}
    assert len(frame.experiment.unique()) == 5
    assert run_grid(path, compare=baselines) == output
    for source in result["sources"][:3]:
        saved = json.loads((Path(source["result"]).parent / "config.json").read_text())
        assert saved["execution"]["seed"] == 0
    artifact = tmp_path / "popularity" / "recommendations.parquet"
    original = artifact.read_bytes()
    artifact.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        run_grid(path, compare=baselines)
    artifact.write_bytes(original)
    baseline = json.loads(baselines[0].read_text())
    baseline["evaluation"]["primary_window_days"] = 30
    baselines[0].write_text(json.dumps(baseline))
    saved_path = tmp_path / "popularity" / "config.json"
    saved = json.loads(saved_path.read_text())
    saved["settings"]["evaluation"]["primary_window_days"] = 30
    saved_path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="conditions differ"):
        run_grid(path, compare=baselines)
    saved_path = tmp_path / "als-32" / "config.json"
    saved = json.loads(saved_path.read_text())
    saved["source_sha256"] = "different-implementation"
    saved_path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="implementation or dependency lock changed"):
        run_grid(path, compare=baselines)
