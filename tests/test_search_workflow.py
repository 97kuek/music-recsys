"""Search budgets, checkpoint failures and immutable retry history without model IO."""

from contextlib import contextmanager

import pytest

from backend.application.search import Search
from backend.domain.experiments import RunStatus
from backend.domain.search import (
    SearchProgress,
    SearchStatus,
    TrialOutcome,
    TrialProgress,
    TrialSpec,
)
from backend.infrastructure.research.search.progress import restore_trial, trial_document


class MemorySearch:
    planned = 3
    max_trials = 3
    time_budget = None
    smoke_passed = True

    def __init__(self, *, fail=False, error=False):
        self.now = 0.0
        self.fail = fail
        self.error = error
        self.states = []
        self.executed = []
        self.consumed = 0
        self.closed = False
        self.prepared = False

    @contextmanager
    def open(self, config, *, smoke):
        try:
            yield self
        finally:
            self.closed = True

    def prepare(self):
        self.prepared = True

    def plans(self):
        for index in range(self.planned):
            self.consumed += 1
            yield TrialSpec({}, index)

    def execute(self, trial):
        self.executed.append(trial.seed)
        self.now += 1
        if self.error:
            raise ValueError("artifact changed")
        failed = self.fail and trial.seed == 1
        return TrialOutcome(
            str(trial.seed), RunStatus.FAILED if failed else RunStatus.SUCCEEDED, trial.seed == 0
        )

    def checkpoint(self, state):
        self.states.append(state)

    def result(self):
        return self.states[-1]


def workflow(backend):
    return Search(backend, clock=lambda: backend.now)


@pytest.mark.parametrize("kind", ["count", "time"])
def test_budget_stops_before_consuming_another_trial(kind):
    backend = MemorySearch()
    if kind == "count":
        backend.max_trials = 1
        backend.planned = 2**25
    else:
        backend.time_budget = 1
    result = workflow(backend).run(None)
    assert result.status == SearchStatus.INCOMPLETE
    assert backend.consumed == 1
    assert result.executed == 0 and result.reused == 1
    assert backend.closed


def test_failed_trial_is_not_reported_as_incomplete_or_success():
    backend = MemorySearch(fail=True)
    backend.max_trials = 2
    result = workflow(backend).run(None)
    assert result.status == SearchStatus.FAILED
    assert result.successful == result.failed == 1
    assert result.executed == result.reused == 1
    assert backend.states[0].outcomes == ()


def test_adapter_failure_checkpoints_failure_and_releases_resources():
    backend = MemorySearch(error=True)
    with pytest.raises(ValueError, match="artifact changed"):
        workflow(backend).run(None)
    assert backend.states[-1].status == SearchStatus.FAILED
    assert backend.closed


def test_smoke_runs_one_trial_and_search_requires_successful_smoke():
    backend = MemorySearch()
    backend.smoke_passed = False
    with pytest.raises(ValueError, match="Smoke"):
        workflow(backend).run(None)
    assert not backend.prepared
    result = workflow(backend).smoke(None)
    assert result.status == SearchStatus.SMOKE_PASSED
    assert backend.consumed == 1


def test_completed_plan_has_terminal_state_and_rejects_duplicate_outcomes():
    state = SearchProgress(1, 1, None)
    result = TrialOutcome("a", RunStatus.SUCCEEDED, False)
    state.record(result, 1)
    with pytest.raises(ValueError, match="duplicate"):
        state.record(result, 1)
    state.finish(1)
    assert state.snapshot().status == SearchStatus.SUCCEEDED
    with pytest.raises(ValueError, match="already finished"):
        state.record(result, 2)


def test_retry_records_interruption_without_mutating_saved_attempts():
    saved = {"status": "running", "attempts": [{"status": "running"}]}
    state = restore_trial(saved)
    prior = state.attempts
    state.start()
    assert saved == {"status": "running", "attempts": [{"status": "running"}]}
    assert prior[0].status == RunStatus.RUNNING
    state.finish(succeeded=False, seconds=2, error_type="StorageError")
    state.start()
    state.finish(succeeded=True, seconds=0)
    document = trial_document(state)
    assert document == {
        "status": "succeeded",
        "seconds": 0,
        "attempts": [
            {"status": "failed", "error_type": "InterruptedRun"},
            {"status": "failed", "seconds": 2, "error_type": "StorageError"},
            {"status": "succeeded", "seconds": 0, "error_type": None},
        ],
    }
    assert trial_document(restore_trial(document)) == document
    with pytest.raises(ValueError, match="successful trial"):
        state.start()
    with pytest.raises(ValueError, match="running"):
        state.finish(succeeded=False, seconds=1)


def test_only_running_trial_can_finish_and_running_outcomes_cannot_be_counted():
    with pytest.raises(ValueError, match="started"):
        TrialProgress().finish(succeeded=True, seconds=0)
    with pytest.raises(ValueError, match="terminal"):
        TrialOutcome("a", RunStatus.RUNNING, False)
    with pytest.raises(ValueError, match="successful"):
        TrialOutcome("a", RunStatus.FAILED, True)
