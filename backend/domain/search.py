"""Budgeted search and retry state, independent of model and persistence adapters."""

import math
from dataclasses import dataclass
from enum import StrEnum

from backend.domain.experiments import RunStatus


class SearchStatus(StrEnum):
    RUNNING = "running"
    SMOKE_PASSED = "smoke_passed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class TrialSpec:
    parameters: dict
    seed: int


@dataclass(frozen=True)
class TrialOutcome:
    key: str
    status: RunStatus
    reused: bool

    def __post_init__(self):
        if self.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
            raise ValueError("trial outcome must be terminal")
        if self.reused and self.status != RunStatus.SUCCEEDED:
            raise ValueError("only verified successful trials can be reused")


@dataclass(frozen=True)
class SearchSnapshot:
    status: SearchStatus
    planned: int
    outcomes: tuple[TrialOutcome, ...]
    elapsed_seconds: float

    @property
    def executed(self) -> int:
        return sum(not trial.reused for trial in self.outcomes)

    @property
    def reused(self) -> int:
        return sum(trial.reused for trial in self.outcomes)

    @property
    def successful(self) -> int:
        return sum(trial.status == RunStatus.SUCCEEDED for trial in self.outcomes)

    @property
    def failed(self) -> int:
        return sum(trial.status == RunStatus.FAILED for trial in self.outcomes)


class SearchProgress:
    def __init__(self, planned: int, max_trials: int, seconds: float | None, *, smoke=False):
        if any(type(value) is not int or value < 1 for value in (planned, max_trials)):
            raise ValueError("trial counts must be positive integers")
        if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
            raise ValueError("time budget must be finite and positive")
        if smoke and (planned != 1 or max_trials != 1):
            raise ValueError("smoke must contain exactly one trial")
        self._planned, self._limit, self._seconds = planned, max_trials, seconds
        self._smoke = smoke
        self._status = SearchStatus.RUNNING
        self._outcomes: dict[str, TrialOutcome] = {}
        self._elapsed = 0.0

    def _running(self):
        if self._status != SearchStatus.RUNNING:
            raise ValueError("search has already finished")

    def _time(self, elapsed):
        if not math.isfinite(elapsed) or elapsed < self._elapsed:
            raise ValueError("elapsed time must be finite and monotonic")
        self._elapsed = elapsed

    def can_start(self, elapsed: float) -> bool:
        self._running()
        self._time(elapsed)
        return len(self._outcomes) < min(self._planned, self._limit) and (
            self._seconds is None or elapsed < self._seconds
        )

    def record(self, outcome: TrialOutcome, elapsed: float) -> None:
        self._running()
        if outcome.key in self._outcomes or len(self._outcomes) >= min(self._planned, self._limit):
            raise ValueError("duplicate trial or trial count exceeded")
        self._time(elapsed)
        self._outcomes[outcome.key] = outcome

    def finish(self, elapsed: float) -> None:
        self._running()
        self._time(elapsed)
        if any(trial.status == RunStatus.FAILED for trial in self._outcomes.values()):
            self._status = SearchStatus.FAILED
        elif len(self._outcomes) == self._planned:
            self._status = SearchStatus.SMOKE_PASSED if self._smoke else SearchStatus.SUCCEEDED
        else:
            self._status = SearchStatus.INCOMPLETE

    def abort(self, elapsed: float) -> None:
        self._running()
        self._time(elapsed)
        self._status = SearchStatus.FAILED

    def snapshot(self) -> SearchSnapshot:
        return SearchSnapshot(
            self._status, self._planned, tuple(self._outcomes.values()), self._elapsed
        )


@dataclass(frozen=True)
class Attempt:
    status: RunStatus
    seconds: float | None = None
    error_type: str | None = None

    def __post_init__(self):
        if self.seconds is not None and (not math.isfinite(self.seconds) or self.seconds < 0):
            raise ValueError("trial duration must be finite and non-negative")
        if self.status == RunStatus.RUNNING and (self.seconds is not None or self.error_type):
            raise ValueError("running attempt cannot have a result")
        if self.status == RunStatus.SUCCEEDED and self.error_type is not None:
            raise ValueError("successful attempt cannot have an error")


class TrialProgress:
    def __init__(self, attempts: tuple[Attempt, ...] = ()):
        if any(attempt.status == RunStatus.RUNNING for attempt in attempts[:-1]):
            raise ValueError("only the last attempt may be running")
        self._attempts = tuple(attempts)

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        return self._attempts

    @property
    def current(self) -> Attempt:
        if not self._attempts:
            raise ValueError("trial has not started")
        return self._attempts[-1]

    def start(self) -> None:
        if self._attempts:
            if self.current.status == RunStatus.SUCCEEDED:
                raise ValueError("successful trial must be reused, not retried")
            if self.current.status == RunStatus.RUNNING:
                self._attempts = (
                    *self._attempts[:-1],
                    Attempt(RunStatus.FAILED, error_type="InterruptedRun"),
                )
        self._attempts += (Attempt(RunStatus.RUNNING),)

    def finish(self, *, succeeded: bool, seconds: float, error_type: str | None = None) -> None:
        if self.current.status != RunStatus.RUNNING:
            raise ValueError("only a running trial can finish")
        completed = Attempt(
            RunStatus.SUCCEEDED if succeeded else RunStatus.FAILED, seconds, error_type
        )
        self._attempts = (*self._attempts[:-1], completed)
