import json
import runpy
from pathlib import Path

import pytest

from music_recsys.configuration import load_settings
from music_recsys.experiment_search import load_study_config
from music_recsys.factor_study import conditions, run_study


def small_study(tmp_path):
    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/bpr-study.json")
    raw.pop("follow_up")
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


def test_study_has_24_bpr_conditions_and_two_follow_ups():
    planned = list(conditions(load_study_config("configs/bpr-study.json")))
    assert len(planned) == 26
    assert len({name for name, _ in planned}) == 26
    assert {trial.seeds for _, trial in planned[:24]} == {(42,), (43,), (44,)}
    assert {trial.parameters["iterations"] for _, trial in planned[:24]} == {30, 100}
    assert {trial.parameters["neighbors"] for _, trial in planned[24:]} == {50, 100}
    assert all(not trial.grid for _, trial in planned)


def test_study_executes_exports_and_resumes_without_retraining(tmp_path):
    import shutil
    import subprocess
    import sys

    if shutil.disk_usage(tmp_path).free < 5 * 1024**3:
        pytest.skip("CLI study has a 5 GiB free-space guard")
    path = small_study(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "music_recsys.factor_study", "--config", str(path)],
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
