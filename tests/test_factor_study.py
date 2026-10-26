import json
import runpy
from pathlib import Path

import pytest

from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.grid import factor_conditions
from src.infrastructure.research.search import load_study_config
from src.interfaces.cli.factor_study import run_study


def small_study(tmp_path, model="bpr"):
    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/bpr-30epoch.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "test-must-not-be-opened"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / ("1epoch" if model == "bpr" else "study")),
    )
    raw["model"]["parameters"]["use_gpu"] = False
    raw["search"].update(grid={"factors": [3], "iterations": [1]}, seeds=[7, 8], max_trials=2)
    if model == "als":
        raw["model"] = dict(name="als", parameters=dict(use_gpu=False))
        raw["search"].update(
            grid=dict(factors=[3], iterations=[1], alpha=[1.0, 10.0], regularization=[0.1]),
            seeds=[7],
        )
    path = tmp_path / "study.json"
    path.write_text(json.dumps(raw))
    return path


def test_studies_separate_epochs_and_share_factors_seeds_and_inputs():
    configs = [load_study_config(f"configs/bpr-{epoch}epoch.json") for epoch in (30, 100, 200, 500)]
    assert configs[0].evaluation == configs[1].evaluation
    assert configs[0].validation_dataset == configs[1].validation_dataset
    assert configs[0].features == configs[1].features
    for config, epoch in zip(configs, (30, 100, 200, 500), strict=True):
        assert config.output.parts[-2:] == ("bpr", f"{epoch}epoch")
        planned = list(factor_conditions(config))
        assert len(planned) == len({name for name, _ in planned}) == 12
        assert {name for name, _ in planned} == {
            f"f{factors}-s{seed}" for factors in (32, 64, 128, 256) for seed in (42, 43, 44)
        }
        assert all(
            trial.name == f"{config.name}-f{trial.parameters['factors']}-e{epoch}-s{trial.seeds[0]}"
            for _, trial in planned
        )
        assert {trial.seeds for _, trial in planned} == {(42,), (43,), (44,)}
        assert {trial.parameters["iterations"] for _, trial in planned} == {epoch}
        assert {trial.parameters["factors"] for _, trial in planned} == {32, 64, 128, 256}
        assert all(not trial.grid and trial.model == "bpr" for _, trial in planned)


def test_mixed_epoch_study_keeps_distinct_condition_directories():
    from dataclasses import replace

    config = load_study_config("configs/bpr-30epoch.json")
    config = replace(config, grid={**config.grid, "iterations": [30, 100]}, max_trials=24)
    planned = list(factor_conditions(config))
    assert len(planned) == len({trial.output for _, trial in planned}) == 24
    assert all(f"-e{trial.parameters['iterations']}-" in name for name, trial in planned)


@pytest.mark.parametrize("model", ["bpr", "als"])
def test_study_executes_exports_and_resumes_without_retraining(tmp_path, model):
    import shutil
    import subprocess
    import sys

    if shutil.disk_usage(tmp_path).free < 5 * 1024**3:
        pytest.skip("CLI study has a 5 GiB free-space guard")
    path = small_study(tmp_path, model)
    subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli.factor_study", "--config", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    output = load_study_config(path).output
    state = json.loads((output / "result.json").read_text())
    assert state["status"] == "succeeded"
    assert state["completed"] == state["planned"] == 2
    root_result = (output / "result.json").read_bytes()
    results = {
        name: (output / name / "result.json").read_bytes()
        for name, _ in factor_conditions(load_study_config(path))
    }
    assert (output / "metrics.csv").is_file()
    assert all((output / name).is_file() for name in state["figures"])
    for name in results:
        child = output / name
        assert (child / "model" / "manifest.json").is_file()
        assert not list(child.glob("*.partial"))
    run_study(path, minimum_free_gb=0)
    assert (output / "result.json").read_bytes() == root_result
    assert all(
        (output / name / "result.json").read_bytes() == value for name, value in results.items()
    )
    raw = json.loads(path.read_text())
    raw["model"]["parameters"]["learning_rate"] = 0.123
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="source/input/plan changed"):
        run_study(path)


def test_low_disk_stops_with_explicit_failure_and_no_fake_success(tmp_path):
    path = small_study(tmp_path)
    with pytest.raises(RuntimeError, match="free disk"):
        run_study(path, minimum_free_gb=10**6)
    output = load_study_config(path).output
    state = json.loads((output / "result.json").read_text())
    assert state["status"] == "failed"
    assert state["completed"] == 0
    assert not (output / "metrics.csv").exists()


@pytest.mark.parametrize("model", ["als", "bpr"])
def test_small_study_adapter_runs_and_verifies_saved_conditions(tmp_path, model):
    path = small_study(tmp_path, model)
    output = run_study(path, minimum_free_gb=0)
    result = (output / "result.json").read_bytes()
    assert json.loads(result)["status"] == "succeeded"
    assert json.loads(result)["completed"] == 2
    assert run_study(path, minimum_free_gb=0) == output
    assert (output / "result.json").read_bytes() == result


def test_als_sensitivity_keeps_shared_population_metrics_and_distinct_conditions():
    config = load_study_config("configs/als-sensitivity.json")
    base = load_study_config("configs/bpr-200epoch.json")
    trials = list(factor_conditions(config))
    assert len(trials) == len({name for name, _ in trials}) == 27
    assert config.evaluation == base.evaluation
    assert config.validation_dataset == base.validation_dataset
    assert config.features == base.features
    assert {trial.parameters["alpha"] for _, trial in trials} == {1.0, 10.0, 100.0}
    assert {trial.parameters["regularization"] for _, trial in trials} == {0.01, 0.1, 1.0}
    assert all(
        trial.parameters["use_gpu"] and trial.parameters["cg_steps"] == 20 for _, trial in trials
    )
    assert len(list(factor_conditions(load_study_config("configs/bpr-500epoch.json")))) == 12
