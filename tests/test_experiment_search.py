"""合成モデルの実ランキングで探索・選択・Test境界を検証する。"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def fixture_factory(name, interactions, *, parameters=None, seed=0):
    from src.domain.recommendation.contracts import RecommendationResponse

    class Fixture:
        def recommend(self, request):
            items = sorted(request.candidate_ids, reverse=parameters.get("reverse", False))
            offset = parameters.get("offset", 0)
            items = items[offset:] + items[:offset]
            return RecommendationResponse(request.request_id, tuple(items[: request.top_k]))

    return Fixture()


def test_smoke_required_and_grid_reuses_successes(study):
    from src.infrastructure.research.search import run_search, select_best, smoke

    config = study()
    with pytest.raises(ValueError, match="Smoke"):
        run_search(config, factory=fixture_factory)
    assert smoke(config, factory=fixture_factory)["status"] == "succeeded"
    first = run_search(config, factory=fixture_factory)
    assert first["status"] == "succeeded"
    assert first["planned_trials"] == 4
    second = run_search(config, factory=fixture_factory)
    assert second["executed_trials"] == 0
    assert second["reused_trials"] == 4
    selected = select_best(config)
    assert selected["seeds"] == [0, 42]
    assert selected["parameters"] in ({"reverse": False}, {"reverse": True})
    assert selected["phase"] == "validation"


def test_two_parameter_grid_budget_and_resume(study):
    from src.infrastructure.research.search import run_search, select_best, smoke

    config = study(grid="reverse=[false,true]\noffset=[0,1]", budget=2, stage="B")
    smoke(config, factory=fixture_factory)
    partial = run_search(config, factory=fixture_factory)
    assert partial["status"] == "incomplete"
    with pytest.raises(ValueError, match="完了"):
        select_best(config)
    config = study(grid="reverse=[false,true]\noffset=[0,1]", budget=20, stage="B")
    done = run_search(config, factory=fixture_factory)
    assert done["status"] == "succeeded"
    assert done["planned_trials"] == 8
    assert done["reused_trials"] >= 2


def test_missing_primary_metric_never_selects(study):
    from src.infrastructure.research.search import run_search, select_best, smoke

    config = study(objective=False)
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    with pytest.raises(ValueError, match="主指標"):
        select_best(config)


def test_selection_review_and_lock_are_required_before_test(study):
    from src.infrastructure.research.search import (
        freeze_selection,
        review_selection,
        run_final_test,
        run_search,
        select_best,
        smoke,
    )

    config = study()
    with pytest.raises(ValueError, match="固定"):
        run_final_test(config, factory=fixture_factory)
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    with pytest.raises(ValueError, match="03"):
        freeze_selection(config)
    review_selection(config, accepted=True, note="Validationの推薦を確認")
    locked = freeze_selection(config)
    assert locked["phase"] == "validation"
    final = run_final_test(config, factory=fixture_factory)
    assert final["phase"] == "test"
    assert final["status"] == "succeeded"
    assert run_final_test(config, factory=fixture_factory)["reused"] is True


def test_changed_data_or_selection_cannot_be_reused(study):
    from src.infrastructure.research.search import load_study, run_search, select_best, smoke

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    raw = json.loads(config.validation_dataset.read_text())
    raw["ground_truth"][0]["relevant_item_ids"] = []
    config.validation_dataset.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="条件"):
        load_study(config)


def test_failed_model_is_recorded_and_can_retry(study):
    from src.infrastructure.research.search import run_search, smoke

    config = study()
    smoke(config, factory=fixture_factory)

    def broken(*args, **kwargs):
        raise RuntimeError("private contents should never be persisted")

    failed = run_search(config, factory=broken)
    assert failed["status"] == "failed"
    assert "private contents" not in json.dumps(failed)
    assert run_search(config, factory=fixture_factory)["status"] == "succeeded"


def test_validation_only_inspection_inherits_parameters(study):
    from src.infrastructure.research.search import (
        inspect_selected,
        run_search,
        select_best,
        smoke,
    )

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    selected = select_best(config)
    context = inspect_selected(config)
    assert context["parameters"] == selected["parameters"]
    assert context["dataset"].provenance["phase"] == "validation"
    assert context["seed"] == 0


def test_test_input_cannot_be_used_for_search(study):
    from src.infrastructure.research.search import smoke

    config = study()
    raw = json.loads(config.validation_dataset.read_text())
    raw["provenance"]["phase"] = "test"
    config.validation_dataset.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Validation"):
        smoke(config, factory=fixture_factory)


def test_test_file_change_is_not_silently_reused(study):
    from src.infrastructure.research.search import (
        freeze_selection,
        review_selection,
        run_final_test,
        run_search,
        select_best,
        smoke,
    )

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    review_selection(config, accepted=True)
    freeze_selection(config)
    run_final_test(config, factory=fixture_factory)
    raw = json.loads(config.test_dataset.read_text())
    raw["ground_truth"][0]["relevant_item_ids"] = []
    config.test_dataset.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Test入力"):
        run_final_test(config, factory=fixture_factory)


def test_rejected_qualitative_review_returns_to_validation(study):
    from src.infrastructure.research.search import (
        review_selection,
        run_search,
        select_best,
        smoke,
    )
    from src.interfaces.notebook.search_analysis import workflow_state

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    review_selection(config, accepted=False, note="推薦を改善したい")
    state = workflow_state(config)
    assert state["status"] == "review_rejected"
    assert "02_analysis" in state["next_action"]


def test_grid_plan_is_bounded_by_budget_without_materializing_combinations(study, monkeypatch):
    import src.infrastructure.research.search as search
    from src.infrastructure.research.search import run_search, smoke

    original = search._plans

    def guarded(config):
        for index, value in enumerate(original(config)):
            if index >= 2:
                raise AssertionError("budget must stop before consuming the whole grid")
            yield value

    monkeypatch.setattr(search, "_plans", guarded)
    config = study(
        grid="\n".join(f"p{i}=[0,1]" for i in range(25)), stage="B", budget=1, seeds="[0]"
    )
    smoke(config, factory=fixture_factory)
    result = run_search(config, factory=fixture_factory)
    assert result["planned_trials"] == 2**25
    assert len(result["planned"]) == 1
    assert result["status"] == "incomplete"


def test_changed_search_range_requires_search_before_reselection(study):
    from src.infrastructure.research.search import load_study, run_search, select_best, smoke

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    changed = study(grid="reverse=[true]")
    with pytest.raises(ValueError, match="探索範囲"):
        load_study(changed)
    assert run_search(changed, factory=fixture_factory)["status"] == "succeeded"


def _locked_study(study):
    from src.infrastructure.research.search import (
        freeze_selection,
        review_selection,
        run_search,
        select_best,
        smoke,
    )

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    review_selection(config, accepted=True)
    freeze_selection(config)
    return config


def test_final_artifact_missing_seed_is_not_a_success(study):
    from src.infrastructure.research.search import read_json, run_final_test, write_json
    from src.interfaces.notebook.search_analysis import final_comparison, workflow_state

    config = _locked_study(study)
    run_final_test(config, factory=fixture_factory)
    path = config.output / "final-test.json"
    final = read_json(path)
    final["trials"].pop(next(iter(final["trials"])))
    write_json(path, final)
    assert workflow_state(config)["status"] == "mismatch"
    with pytest.raises(ValueError, match="条件|seed"):
        final_comparison([config])


def test_failed_test_retries_only_failed_seeds_and_preserves_attempts(study):
    from src.infrastructure.research.search import run_final_test

    config = _locked_study(study)

    def broken_seed(*args, **kwargs):
        if kwargs["seed"] == 42:
            raise RuntimeError("transient")
        return fixture_factory(*args, **kwargs)

    assert run_final_test(config, factory=broken_seed)["status"] == "failed"
    called = []

    def record(*args, **kwargs):
        called.append(kwargs["seed"])
        return fixture_factory(*args, **kwargs)

    final = run_final_test(config, factory=record)
    assert called == [42]
    assert final["status"] == "succeeded"
    retried = next(t for t in final["trials"].values() if t["seed"] == 42)
    assert [x["status"] for x in retried["attempts"]] == ["failed", "succeeded"]


def test_missing_test_results_are_not_displayed_as_success(study):
    from src.infrastructure.research.search import run_final_test
    from src.interfaces.notebook.search_analysis import workflow_state

    config = _locked_study(study)
    final = run_final_test(config, factory=fixture_factory)
    trial = next(iter(final["trials"].values()))
    (config.output / trial["curves"]).unlink()
    assert workflow_state(config)["status"] == "mismatch"


def test_smoke_is_small_and_separate_from_validation_search(study):
    from dataclasses import replace

    from src.infrastructure.research.search import read_json, run_search, smoke

    config = replace(study(), smoke_users=1, smoke_train_users=1)
    seen = []

    def record(name, interactions, **kwargs):
        seen.append(len({x.user_id for x in interactions}))
        return fixture_factory(name, interactions, **kwargs)

    result = smoke(config, factory=record)
    stored = read_json(config.output / "study.json")
    trial = stored["trials"][result["trial_id"]]
    report = read_json(config.output / trial["report"])
    assert seen == [1]
    assert len(report["runs"]) == 1
    assert trial["phase"] == "smoke"
    assert run_search(config, factory=record)["executed_trials"] == 4


def test_test_rejects_validation_history_missing_from_requests(study):
    from src.infrastructure.research.search import (
        freeze_selection,
        review_selection,
        run_final_test,
        run_search,
        select_best,
        smoke,
    )

    config = study()
    test = json.loads(config.test_dataset.read_text())
    validation = json.loads(config.validation_dataset.read_text())
    for request, old in zip(test["requests"], validation["requests"], strict=True):
        request["history"] = old["history"]
    config.test_dataset.write_text(json.dumps(test))
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    review_selection(config, accepted=True)
    freeze_selection(config)
    with pytest.raises(ValueError, match="履歴"):
        run_final_test(config, factory=fixture_factory)


def test_stage_c_cannot_change_fixed_parameters(study, tmp_path):
    from dataclasses import replace

    from src.infrastructure.research.search import run_search, smoke

    parent = replace(study(), parameters={"offset": 0})
    smoke(parent, factory=fixture_factory)
    run_search(parent, factory=fixture_factory)
    child = replace(
        parent,
        stage="C",
        parent=parent.output / "study.json",
        output=tmp_path / "child",
        parameters={"offset": 1},
    )
    smoke(child, factory=fixture_factory)
    with pytest.raises(ValueError, match="固定パラメータ"):
        run_search(child, factory=fixture_factory)
