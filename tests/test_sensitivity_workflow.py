"""Sensitivity orchestration with replaceable resources and failure boundaries."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from src.application.experiments import Sensitivity


class MemorySensitivity:
    output = Path("unused")
    conditions = ("control", "cached", "new")
    batches = ()

    def __init__(self, *, complete=False, failure=None, mismatch=False):
        self.complete = complete
        self.failure = failure
        self.mismatch = mismatch
        self.saved = {"control", "cached"}
        self.calls = []
        self.published = None
        self.closed = False

    @contextmanager
    def open(self, source):
        try:
            yield self
        finally:
            self.closed = True

    def step(self, name):
        self.calls.append(name)
        if self.failure == name:
            raise RuntimeError(name)

    def check_space(self, minimum_free_gb):
        self.step("space")

    def batch_completed(self, batch):
        return batch == (1.2, 0.75)

    def execute_batch(self, batch):
        self.step("batch")
        self.saved.add("new")

    def completed(self, name):
        return name in self.saved

    def execute(self, name):
        self.step(f"execute:{name}")
        self.saved.add(name)

    def verify(self, name):
        self.step(f"verify:{name}")
        return {
            "definitions": {
                "metric": "changed" if self.mismatch and name == "new" else "same",
                "ranking": name,
                "latency": name,
            }
        }, {"name": name}

    def publish(self, finished):
        self.step("publish")
        self.published = finished


def test_sensitivity_reuses_controls_and_completed_trials_before_publication():
    backend = MemorySensitivity()
    assert Sensitivity(backend).run("config") == backend.output
    assert backend.calls == [
        "verify:control",
        "verify:cached",
        "space",
        "execute:new",
        "verify:new",
        "publish",
    ]
    assert [entry[0] for entry in backend.published] == list(backend.conditions)
    assert backend.closed


@pytest.mark.parametrize(
    "failure", ["space", "execute:new", "verify:control", "verify:new", "publish"]
)
def test_sensitivity_failure_closes_resources_without_publishing(failure):
    backend = MemorySensitivity(failure=failure)
    with pytest.raises(RuntimeError, match=failure):
        Sensitivity(backend).run("config")
    assert backend.published is None
    assert backend.closed


def test_sensitivity_rejects_different_evaluation_definitions():
    backend = MemorySensitivity(mismatch=True)
    with pytest.raises(ValueError, match="definitions differ"):
        Sensitivity(backend).run("config")
    assert backend.published is None
    assert backend.closed


def test_published_comparison_is_verified_without_executing_or_republishing():
    backend = MemorySensitivity(complete=True)
    Sensitivity(backend).run("config")
    assert backend.calls == [f"verify:{name}" for name in backend.conditions]
    assert backend.published is None


def test_shared_batches_skip_completed_groups_and_avoid_duplicate_execution():
    backend = MemorySensitivity()
    backend.batches = ((1.2, 0.75), (1.2, 0.5))
    Sensitivity(backend).run("config")
    assert backend.calls == [
        "space",
        "batch",
        "verify:control",
        "verify:cached",
        "verify:new",
        "publish",
    ]


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_space_limit_does_not_open_resources(value):
    backend = MemorySensitivity()
    with pytest.raises(ValueError, match="finite and non-negative"):
        Sensitivity(backend).run("config", minimum_free_gb=value)
    assert not backend.closed
    assert not backend.calls
