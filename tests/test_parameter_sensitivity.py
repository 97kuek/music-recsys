import json
import runpy
from dataclasses import replace
from pathlib import Path

import pytest

from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.search import load_study_config
from src.interfaces.cli.sensitivity import conditions, controls, run_study, verify


@pytest.mark.parametrize(
    ("name", "count", "controls_count"),
    [
        ("ease-sensitivity", 2, 1),
        ("als-boundary", 3, 3),
        ("bpr-sensitivity", 12, 3),
        ("bm25-sensitivity", 3, 1),
    ],
)
def test_plans_preserve_shared_evaluation_and_only_add_requested_conditions(
    name, count, controls_count
):
    config = load_study_config(f"configs/{name}.json")
    common = load_study_config("configs/ease.json")
    planned = list(conditions(config))
    assert len(planned) == len({trial.output for _, trial in planned}) == count
    assert len(list(controls(config))) == controls_count
    assert config.evaluation == common.evaluation
    assert config.validation_dataset == common.validation_dataset
    assert config.features == common.features
    assert all(trial.parameters["use_gpu"] and not trial.grid for _, trial in planned)
    assert config.task is config.metric is None
    if name == "bpr-sensitivity":
        assert {
            (trial.parameters["learning_rate"], trial.parameters["regularization"])
            for _, trial in planned
        } == {(0.003, 0.01), (0.03, 0.01), (0.01, 0.001), (0.01, 0.1)}
        assert {trial.seeds[0] for _, trial in planned} == {42, 43, 44}


def small_study(tmp_path, *, include_control=False):
    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/bm25-sensitivity.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "test-must-not-open"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "study"),
    )
    raw["model"]["parameters"].update(use_gpu=False, neighbors=3)
    raw["search"].update(conditions=[{"b": 0.5}], controls=[], max_trials=1, seeds=[0])
    path = tmp_path / "study.json"
    path.write_text(json.dumps(raw))
    if include_control:
        from src.interfaces.cli.evaluate import run

        base = replace(load_study_config(path), output=tmp_path / "control")
        run(base)
        saved = tmp_path / "control" / "config.json"
        document = json.loads(saved.read_text())
        document["source_sha256"] = "a" * 64
        document["dependency_lock_sha256"] = "b" * 64
        saved.write_text(json.dumps(document))
        raw["search"]["controls"] = [dict(output=str(tmp_path / "control"), parameters={}, seed=0)]
        path.write_text(json.dumps(raw))
    return path


def test_executes_resumes_verifies_and_cites_historical_control_without_changing_it(tmp_path):
    path = small_study(tmp_path, include_control=True)
    config = load_study_config(path)
    control = tmp_path / "control" / "config.json"
    control_before = control.read_bytes()
    output = run_study(path, minimum_free_gb=0)
    marker = output / "result.json"
    document = json.loads(marker.read_text())
    assert document["status"] == "succeeded"
    assert document["planned"] == document["completed"] == 1
    assert document["sources"]["control-n3-k1.2-b0.75"]["source_sha256"] == "a" * 64
    assert document["sources"]["control-n3-k1.2-b0.75"]["dependency_lock_changed"]
    assert control.read_bytes() == control_before
    assert (output / "metrics.csv").is_file()
    assert all((output / file).is_file() for file in document["figures"])
    assert not list(output.glob("*/model"))
    before = marker.read_bytes()
    run_study(path, minimum_free_gb=0)
    assert marker.read_bytes() == before
    _, trial = next(conditions(config))
    (trial.output / "recommendations.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        run_study(path, minimum_free_gb=0)


def test_partial_failure_does_not_publish_comparison(tmp_path, monkeypatch):
    import subprocess

    path = small_study(tmp_path)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "trial")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        run_study(path, minimum_free_gb=0)
    output = load_study_config(path).output
    assert not (output / "result.json").exists()
    assert not (output / "metrics.csv").exists()
    assert not list(output.glob("*.png"))


def test_incomplete_trial_and_changed_plan_require_inspection(tmp_path):
    path = small_study(tmp_path)
    config = load_study_config(path)
    _, trial = next(conditions(config))
    trial.output.mkdir(parents=True)
    (trial.output / "run.log").write_text("interrupted")
    with pytest.raises(ValueError, match="incomplete condition"):
        run_study(path, minimum_free_gb=0)
    raw = json.loads(path.read_text())
    raw["search"]["conditions"] = [{"b": 1.0}]
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="source/input/plan changed"):
        run_study(path, minimum_free_gb=0)


def test_duplicate_deterministic_seeds_are_rejected():
    config = load_study_config("configs/ease-sensitivity.json")
    with pytest.raises(ValueError, match="duplicate"):
        list(conditions(replace(config, seeds=(0, 1), max_trials=4)))


def test_historical_control_parameters_are_still_checked(tmp_path):
    path = small_study(tmp_path, include_control=True)
    _, trial = next(controls(load_study_config(path)))
    with pytest.raises(ValueError, match="input/parameters/population changed"):
        verify(replace(trial, parameters={**trial.parameters, "b": 0.0}), historical=True)


@pytest.mark.parametrize(("name", "count"), [("content-sensitivity", 4), ("bm25-grid", 75)])
def test_publication_plans_are_complete_unselected_and_have_a_same_source_baseline(name, count):
    config = load_study_config(f"configs/{name}.json")
    planned = list(conditions(config))
    assert len(planned) == count
    assert len({name for name, _ in planned}) == count
    assert all(c.task is c.metric is None for _, c in planned)
    assert all(c.parameters["use_gpu"] and c.seeds == (0,) for _, c in planned)
    assert not list(controls(config))
    assert planned[0][1].parameters == config.parameters
    if config.model == "content":
        assert {(c.parameters["use_idf"], c.parameters["aggregation"]) for _, c in planned} == {
            (True, "binary"),
            (True, "log_count"),
            (False, "binary"),
            (False, "log_count"),
        }
    else:
        assert {c.parameters["neighbors"] for _, c in planned} == {10, 20, 50, 100, 200}
        assert {c.parameters["k1"] for _, c in planned} == {0.3, 1.2, 4.8}
        assert {c.parameters["b"] for _, c in planned} == {0, 0.25, 0.5, 0.75, 1}


def test_content_publication_evaluates_all_settings_and_never_opens_test(tmp_path):
    raw = load_settings("configs/content-sensitivity.json")
    bundle = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"](tmp_path / "bundle")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "never-open-test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "study"),
    )
    raw["model"]["parameters"]["use_gpu"] = False
    path = tmp_path / "content.json"
    path.write_text(json.dumps(raw))
    output = run_study(path, minimum_free_gb=0)
    report = json.loads((output / "result.json").read_text())
    assert report["completed"] == 4
    assert report["phase"] == "validation"
    assert (output / "paired.csv").is_file()
    assert all((output / p).is_file() for p in report["figures"])
    assert "Exploratory" in report["inference"]
    assert not (tmp_path / "never-open-test").exists()
    before = (output / "result.json").read_bytes()
    run_study(path, minimum_free_gb=0)
    assert (output / "result.json").read_bytes() == before
