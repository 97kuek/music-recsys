"""Exercise workflow contracts with memory adapters instead of model or filesystem IO."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from src.application.experiments import Evaluation, FactorStudy


class MemoryEvaluation:
    output = Path("unused")

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure
        self.result = {"status": "succeeded"}
        self.published = None

    @contextmanager
    def open(self, source, options):
        self.calls.append("open")
        try:
            yield self
        finally:
            self.calls.append("close")

    def _step(self, name):
        self.calls.append(name)
        if name == self.failure:
            raise RuntimeError(name)

    def train(self):
        self._step("train")

    def score(self):
        self._step("score")
        return self.result

    def render(self, result):
        self._step("render")
        result["figures"] = ["recall.png"]

    def verify(self):
        self._step("verify")

    def export(self, result):
        self._step("export")
        result["serving_model"] = "verified"

    def publish(self, result):
        self._step("publish")
        self.published = deepcopy(result)


def test_evaluation_publishes_only_after_render_verification_and_export():
    store = MemoryEvaluation()
    assert Evaluation(store).run("config") == store.output
    assert store.calls == [
        "open",
        "train",
        "score",
        "render",
        "verify",
        "export",
        "publish",
        "close",
    ]
    assert store.published == {
        "status": "succeeded",
        "figures": ["recall.png"],
        "serving_model": "verified",
    }


@pytest.mark.parametrize("failure", ["train", "score", "render", "verify", "export"])
def test_evaluation_failure_releases_session_without_publishing(failure):
    store = MemoryEvaluation(failure)
    with pytest.raises(RuntimeError, match=failure):
        Evaluation(store).run("config")
    assert store.published is None
    assert "publish" not in store.calls
    assert store.calls[-1] == "close"


class MemoryStudy:
    output = Path("unused")
    conditions = ("a", "b")

    def __init__(self, *, complete=False, failure=None):
        self.complete = complete
        self.failure = failure
        self.saved = {"a"}
        self.executed = []
        self.verified = []
        self.states = []
        self.closed = False
        self.messages = []
        self.summary = None

    @contextmanager
    def open(self, source):
        try:
            yield self
        finally:
            self.closed = True

    def check_space(self, minimum_free_gb):
        pass

    def completed(self, name):
        return name in self.saved

    def execute(self, name):
        self.executed.append(name)
        self.saved.add(name)

    def verify(self, name):
        self.verified.append(name)
        if self.failure == name:
            raise ValueError("artifact changed")
        return {"name": name}

    def evidence(self, name, result):
        return {"condition": name, "status": "succeeded"}

    def summarize(self, finished):
        self.summary = finished
        if self.failure == "summary":
            raise ValueError("plot failed")
        return ["recall.png"]

    def checkpoint(self, state):
        self.states.append(deepcopy(state))

    def record(self, message):
        self.messages.append(message)


def test_study_reuses_existing_trial_and_summarizes_verified_results():
    store = MemoryStudy()
    assert FactorStudy(store).run("config") == store.output
    assert store.executed == ["b"]
    assert store.verified == ["a", "b"]
    assert store.summary == [("a", {"name": "a"}), ("b", {"name": "b"})]
    assert store.states[-1]["status"] == "succeeded"
    assert store.states[-1]["completed"] == 2
    assert store.closed


def test_completed_study_still_verifies_each_trial_without_rewriting():
    store = MemoryStudy(complete=True)
    FactorStudy(store).run("config")
    assert store.verified == ["a", "b"]
    assert not store.executed and not store.states
    assert store.summary is None and store.closed


@pytest.mark.parametrize("failure,completed", [("a", 0), ("b", 1), ("summary", 2)])
def test_study_failure_never_checkpoints_success(failure, completed):
    store = MemoryStudy(failure=failure)
    with pytest.raises(ValueError):
        FactorStudy(store).run("config")
    assert store.states[-1]["status"] == "failed"
    assert store.states[-1]["completed"] == completed
    assert not any(state["status"] == "succeeded" for state in store.states)
    assert store.closed


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf")])
def test_invalid_disk_budget_is_rejected_before_opening(budget):
    store = MemoryStudy()
    with pytest.raises(ValueError, match="finite and non-negative"):
        FactorStudy(store).run("config", minimum_free_gb=budget)
    assert not store.closed and not store.states
