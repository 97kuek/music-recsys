import json
import runpy
from pathlib import Path

import pytest

from src.configuration import load_settings
from src.experiment_search import load_study_config
from src.factor_study import conditions, run_study


def small_study(tmp_path):
    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/bpr-30epoch.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "test-must-not-be-opened"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "study"),
    )
    raw["model"]["parameters"]["use_gpu"] = False
    raw["search"].update(grid={"factors": [3], "iterations": [1]}, seeds=[7, 8], max_trials=2)
    path = tmp_path / "study.json"
    path.write_text(json.dumps(raw))
    return path


def test_studies_separate_epochs_and_share_factors_seeds_and_inputs():
    configs = [load_study_config(f"configs/bpr-{epoch}epoch.json") for epoch in (30, 100, 200)]
    assert configs[0].evaluation == configs[1].evaluation
    assert configs[0].validation_dataset == configs[1].validation_dataset
    assert configs[0].features == configs[1].features
    for config, epoch in zip(configs, (30, 100, 200), strict=True):
        planned = list(conditions(config))
        assert len(planned) == len({name for name, _ in planned}) == 12
        assert {trial.seeds for _, trial in planned} == {(42,), (43,), (44,)}
        assert {trial.parameters["iterations"] for _, trial in planned} == {epoch}
        assert {trial.parameters["factors"] for _, trial in planned} == {32, 64, 128, 256}
        assert all(not trial.grid and trial.model == "bpr" for _, trial in planned)


def test_study_executes_exports_and_resumes_without_retraining(tmp_path):
    import shutil
    import subprocess
    import sys

    if shutil.disk_usage(tmp_path).free < 5 * 1024**3:
        pytest.skip("CLI study has a 5 GiB free-space guard")
    path = small_study(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "src.factor_study", "--config", str(path)],
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
        for name, _ in conditions(load_study_config(path))
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
