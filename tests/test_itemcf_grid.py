import json
from pathlib import Path

import pytest

from src.infrastructure.research.configuration import load_settings


def test_grid_expands_conditions_and_reuses_completed_outputs(tmp_path, monkeypatch):
    import src.interfaces.cli.grid as grid
    from src.interfaces.cli.experiment_search import load_study_config

    raw = load_settings(Path("configs/itemcf.json"))
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


def test_itemcf_report_failure_reuse_and_artifact_guard(tmp_path, monkeypatch):
    import runpy

    from src.interfaces.cli import grid
    from src.interfaces.notebook import itemcf_report

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/itemcf.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "never-test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "results"),
    )
    raw["search"]["grid"] = dict(similarity=["cosine"], neighbors=[20])
    path = tmp_path / "itemcf.json"
    path.write_text(json.dumps(raw))
    draw = itemcf_report.comparison_curves

    def fail(*args):
        raise RuntimeError("plot failed")

    monkeypatch.setattr(itemcf_report, "comparison_curves", fail)
    with pytest.raises(RuntimeError, match="plot failed"):
        grid.run_grid(path)
    assert not (tmp_path / "results/result.json").exists()
    assert not (tmp_path / "results/metrics.csv").exists()
    monkeypatch.setattr(itemcf_report, "comparison_curves", draw)
    output = grid.run_grid(path)
    before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    assert grid.run_grid(path) == output
    assert before == {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    result = json.loads((output / "result.json").read_text())
    artifact = Path(result["sources"][0]["result"]).parent / "recommendations.parquet"
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="artifact changed"):
        grid.run_grid(path)
