"""Progress invariants and compatibility of persisted factor-study records."""

from dataclasses import FrozenInstanceError

import pytest

from src.domain.experiments import ConditionResult, Progress, RunStatus
from src.infrastructure.research.studies import progress_document


def result(name):
    return ConditionResult(name, f"{name}/result.json", "a" * 64, None, 0.0)


@pytest.mark.parametrize("plan", [(), ("a", "a"), ("",), (None,)])
def test_invalid_plans_are_rejected(plan):
    with pytest.raises(ValueError):
        Progress(plan)


def test_active_condition_must_finish_before_another_can_start():
    state = Progress(("a", "b"))
    with pytest.raises(ValueError, match="active"):
        state.complete(result("a"))
    with pytest.raises(ValueError, match="unknown"):
        state.start("unknown")
    state.start("a")
    with pytest.raises(ValueError, match="has not completed"):
        state.start("b")
    with pytest.raises(ValueError, match="active"):
        state.complete(result("b"))
    assert state.completed == 0
    state.complete(result("a"))
    with pytest.raises(ValueError, match="active"):
        state.complete(result("a"))
    with pytest.raises(ValueError, match="already completed"):
        state.start("a")
    assert state.completed == 1


def test_only_all_verified_conditions_can_succeed():
    state = Progress(("a", "b"))
    state.start("a")
    state.complete(result("a"))
    with pytest.raises(ValueError, match="all planned"):
        state.succeed([])
    state.start("b")
    state.complete(result("b"))
    figures = ["recall.png"]
    state.succeed(figures)
    figures.append("later.png")
    saved = state.snapshot()
    assert saved.status is RunStatus.SUCCEEDED
    assert saved.current is None
    assert saved.planned == saved.completed == 2
    assert saved.figures == ("recall.png",)


@pytest.mark.parametrize("success", [False, True])
def test_terminal_states_reject_further_changes(success):
    state = Progress(("a",))
    state.start("a")
    state.complete(result("a"))
    if success:
        state.succeed([])
    else:
        state.fail("plot failed")
    before = state.snapshot()
    for action in (
        lambda: state.start("a"),
        lambda: state.complete(result("a")),
        lambda: state.succeed([]),
        lambda: state.fail("another error"),
    ):
        with pytest.raises(ValueError, match="already finished"):
            action()
    assert state.snapshot() == before


def test_snapshots_do_not_change_as_work_progresses():
    state = Progress(("a",))
    before = state.snapshot()
    state.start("a")
    state.complete(result("a"))
    with pytest.raises(FrozenInstanceError):
        before.status = RunStatus.SUCCEEDED
    with pytest.raises(FrozenInstanceError):
        state.snapshot().conditions[0].condition = "b"
    assert before.completed == 0 and before.current is None
    assert state.completed == 1


def test_json_contract_retains_missing_and_zero_measurements():
    state = Progress(("a",))
    initial = progress_document(state.snapshot())
    assert initial == {
        "format": "music-recsys-factor-study-v1",
        "status": "running",
        "phase": "validation",
        "planned": 1,
        "completed": 0,
        "conditions": [],
        "figures": [],
    }
    state.start("a")
    state.complete(result("a"))
    state.succeed(["recall.png"])
    assert progress_document(state.snapshot()) == {
        **initial,
        "status": "succeeded",
        "completed": 1,
        "current": None,
        "figures": ["recall.png"],
        "conditions": [
            {
                "condition": "a",
                "status": "succeeded",
                "result": "a/result.json",
                "result_sha256": "a" * 64,
                "training_seconds": None,
                "evaluation_seconds": 0.0,
            }
        ],
    }


@pytest.mark.parametrize("started", [False, True])
def test_failure_document_preserves_current_condition_and_error(started):
    state = Progress(("a",))
    if started:
        state.start("a")
    state.fail("RuntimeError: disk full")
    document = progress_document(state.snapshot())
    assert document["status"] == "failed"
    assert document["error"] == "RuntimeError: disk full"
    assert document["completed"] == 0
    assert ("current" in document) == started
    if started:
        assert document["current"] == "a"
