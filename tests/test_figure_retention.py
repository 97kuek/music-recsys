import json
from pathlib import Path

import pytest

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.figure_retention import (
    figure_references,
    prune_figures,
    retention_record,
)
from src.infrastructure.research.store import atomic_json


def example(tmp_path):
    directory = tmp_path / "condition"
    directory.mkdir()
    atomic_json(directory / "config.json", dict(settings={}))
    result = dict(
        format="music-recsys-full-result-v1",
        status="succeeded",
        primary=dict(primary_window_days=90, primary_day_weight=1),
        accuracy=[
            dict(
                task="discovery",
                metric="recall",
                window_days=90,
                day_weight=None,
                k=20,
                mean=0,
                ci_low=0,
                ci_high=0,
            )
        ],
        diversity=[],
        efficiency={},
        observations=[],
        figures=["recall.png", "recall_heatmap.png"],
    )
    atomic_json(directory / "result.json", result)
    (directory / "metrics.csv").write_text("metric,mean\nrecall,0\n", encoding="utf-8")
    for name in result["figures"]:
        (directory / name).write_bytes(b"synthetic image " + name.encode())
    condition = dict(
        sources={
            name: dict(sha256=file_sha256(directory / name))
            for name in ("config.json", "result.json", "metrics.csv")
        },
        figures={
            name: dict(
                sha256=file_sha256(directory / name), bytes=(directory / name).stat().st_size
            )
            for name in result["figures"]
        },
    )
    plan = tmp_path / "plan.json"
    atomic_json(
        plan,
        dict(
            format="music-recsys-figure-retention-plan-v1",
            conditions={str(directory.resolve()): condition},
        ),
    )
    return directory, plan


def prune(directory, plan):
    return prune_figures(
        directory,
        manifest=plan,
        expected_manifest_sha256=file_sha256(plan),
        reason="explicitly authorized cleanup of redrawable individual plots",
    )


def test_prune_preserves_numeric_evidence_and_references_recorded_missing_figures(tmp_path):
    directory, plan = example(tmp_path)
    before = {
        name: (directory / name).read_bytes()
        for name in ("config.json", "result.json", "metrics.csv")
    }
    assert all(r["state"] == "available" for r in figure_references(directory, ["recall.png"]))
    result = prune(directory, plan)
    assert result["status"] == "pruned"
    assert not list(directory.glob("*.png"))
    assert before == {name: (directory / name).read_bytes() for name in before}
    refs = figure_references(directory, ["recall.png", "recall_heatmap.png"])
    assert all(
        r["state"] == "pruned"
        and r["sha256"] == result["removed_files"][Path(r["path"]).name]["sha256"]
        for r in refs
    )
    assert prune(directory, plan) == result


def test_missing_unrecorded_and_forged_retention_remain_corruption(tmp_path):
    directory, plan = example(tmp_path)
    (directory / "recall.png").unlink()
    with pytest.raises(ValueError, match="without retention"):
        figure_references(directory, ["recall.png"])
    with pytest.raises(ValueError, match="before pruning"):
        prune(directory, plan)
    other = tmp_path / "other"
    other.mkdir()
    directory, plan = example(other)
    prune(directory, plan)
    marker = directory / "figures.retention.json"
    record = json.loads(marker.read_text())
    record["removed_files"]["recall.png"]["sha256"] = "0" * 64
    atomic_json(marker, record)
    with pytest.raises(ValueError, match="file evidence changed"):
        retention_record(directory)


def test_changed_numerical_source_or_external_plan_rejects_pruned_evidence(tmp_path):
    directory, plan = example(tmp_path)
    prune(directory, plan)
    (directory / "metrics.csv").write_text("changed")
    with pytest.raises(ValueError, match="source evidence changed"):
        retention_record(directory)
    other = tmp_path / "other"
    other.mkdir()
    directory, plan = example(other)
    prune(directory, plan)
    plan.write_text(plan.read_text() + " ")
    with pytest.raises(ValueError, match="plan hash changed"):
        retention_record(directory)


def test_interrupted_pruning_resumes_only_the_same_verified_operation(tmp_path, monkeypatch):
    directory, plan = example(tmp_path)
    original = Path.unlink
    calls = []

    def interrupt(path, *args, **kwargs):
        if path.suffix == ".png":
            calls.append(path)
            if len(calls) == 2:
                raise OSError("simulated interruption")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError, match="simulated"):
            prune(directory, plan)
    with pytest.raises(ValueError, match="incomplete"):
        retention_record(directory)
    assert prune(directory, plan)["status"] == "pruned"


def test_external_and_unmeasured_figures_are_not_prunable(tmp_path):
    directory, plan = example(tmp_path)
    with pytest.raises(ValueError, match="basename"):
        figure_references(directory, ["../escape.png"])
    result_path = directory / "result.json"
    result = json.loads(result_path.read_text())
    result["accuracy"][0]["mean"] = None
    atomic_json(result_path, result)
    record = json.loads(plan.read_text())
    record["conditions"][str(directory.resolve())]["sources"]["result.json"]["sha256"] = (
        file_sha256(result_path)
    )
    atomic_json(plan, record)
    with pytest.raises(ValueError, match="measured data"):
        prune(directory, plan)


def test_redraw_to_new_output_uses_saved_values_without_mutating_source(tmp_path):
    import matplotlib.pyplot as plt

    from src.interfaces.cli.figure_redraw import redraw

    directory, plan = example(tmp_path)
    result_path = directory / "result.json"
    result = json.loads(result_path.read_text())
    result["model"] = "popularity"
    result["efficiency"] = dict(
        model="popularity",
        latency_mean=None,
        input_tokens=None,
        output_tokens=None,
        api_cost=None,
        llm_calls=None,
        tool_calls=None,
    )
    result["diversity"] = [
        dict(task=task, k=20, ild=None, catalog_coverage=None, novelty=None)
        for task in ("new", "repeat")
    ]
    result["observations"] = [
        dict(
            phase="validation",
            window_days=90,
            task=task,
            observation=observation,
            bin=label,
            pairs=1,
        )
        for task in ("new", "repeat")
        for observation in ("evaluation_play_count", "evaluation_active_days")
        for label in ("1", "2", "3–4", "5–9", "10–19", "20+")
    ]
    atomic_json(result_path, result)
    record = json.loads(plan.read_text())
    record["conditions"][str(directory.resolve())]["sources"]["result.json"]["sha256"] = (
        file_sha256(result_path)
    )
    atomic_json(plan, record)
    prune(directory, plan)
    before = {
        name: file_sha256(directory / name)
        for name in ("result.json", "config.json", "metrics.csv", "figures.retention.json")
    }
    output = redraw(result_path, tmp_path / "redrawn", filenames=["recall.png"])
    assert (output / "recall.png").is_file()
    generated = json.loads((output / "result.json").read_text())
    assert generated["status"] == "succeeded"
    assert set(generated["artifacts"]) == {"recall.png"}
    assert before == {name: file_sha256(directory / name) for name in before}
    with pytest.raises(FileExistsError):
        redraw(result_path, output)
    plt.close("all")


def test_catalog_and_additional_validation_publish_explicit_pruned_references(tmp_path):
    from src.interfaces.cli.additional_validation import _Sources

    directory, plan = example(tmp_path)
    prune(directory, plan)
    sources = _Sources(tmp_path / "output", 0, {})
    sources.figure_refs(directory, json.loads((directory / "result.json").read_text()))
    assert len(sources.figures) == 2
    assert all(
        r["state"] == "pruned" and not Path(r["path"]).is_absolute()
        for r in sources.figures.values()
    )


def test_catalog_keeps_pruned_conditions_without_rewriting_original_result(tmp_path):
    import runpy

    from src.interfaces.cli.catalog import run

    create = runpy.run_path("tests/test_experiment_catalog.py")["experiments"]
    method = create(tmp_path)
    directory = method / "plays"
    filenames = ["recall.png", "repeat_recall.png"]
    condition = dict(
        sources={
            name: dict(sha256=file_sha256(directory / name))
            for name in ("config.json", "result.json", "metrics.csv")
        },
        figures={
            name: dict(
                sha256=file_sha256(directory / name), bytes=(directory / name).stat().st_size
            )
            for name in filenames
        },
    )
    plan = tmp_path / "plan.json"
    atomic_json(
        plan,
        dict(
            format="music-recsys-figure-retention-plan-v1",
            conditions={str(directory.resolve()): condition},
        ),
    )
    before = file_sha256(directory / "result.json")
    prune(directory, plan)
    output = run(method)
    result = json.loads((output / "result.json").read_text())
    removed = [r for r in result["figures"] if r["state"] == "pruned"]
    assert len(removed) == 2 and all(r["condition"] == "plays" for r in removed)
    assert file_sha256(directory / "result.json") == before
