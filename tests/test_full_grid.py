import json
from pathlib import Path

import pytest


def test_grid_expands_conditions_and_reuses_completed_outputs(tmp_path, monkeypatch):
    import music_recsys.full_grid as grid
    from music_recsys.experiment_search import load_study_config

    raw = json.loads(Path("configs/itemcf.json").read_text())
    raw["experiment"].update(
        validation_dataset=str(tmp_path / "data/benchmarks/music4all/validation"),
        test_dataset=str(tmp_path / "data/benchmarks/music4all/test"),
        features=str(tmp_path / "features.json"),
        output=str(tmp_path / "data/experiments/itemcf"),
    )
    path = tmp_path / "itemcf.json"
    path.write_text(json.dumps(raw))
    calls = []

    def execute(path, *, model_cache):
        c = load_study_config(path)
        calls.append((c.parameters, c.output, model_cache))
        c.output.mkdir(parents=True)
        (c.output / "result.json").write_text("{}")

    monkeypatch.setattr(grid, "run", execute)
    reports = []
    monkeypatch.setattr(grid, "build_grid_report", lambda p: reports.append(p))
    verified = []
    monkeypatch.setattr(grid, "read_result", lambda c: verified.append(c.output))
    grid.run_grid(path)
    assert len(calls) == 6
    assert {(c["similarity"], c["neighbors"]) for c, _, _ in calls} == {
        (s, n) for s in ("cosine", "jaccard") for n in (20, 50, 100)
    }
    assert len({output for _, output, _ in calls}) == 6
    assert len({cache for _, _, cache in calls}) == 1
    grid.run_grid(path)
    assert len(calls) == 6 and len(verified) == 6
    assert reports == [path, path]
    raw["search"]["max_trials"] = 5
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="max_trials"):
        grid.run_grid(path)
