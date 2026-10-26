"""Progress and valid transitions for a sequential experiment plan."""

from dataclasses import dataclass
from enum import StrEnum


class RunStatus(StrEnum):
    RUNNING = "running"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


@dataclass(frozen=True)
class ConditionResult:
    condition: str
    result: str
    result_sha256: str
    training_seconds: float | None
    evaluation_seconds: float | None


@dataclass(frozen=True)
class ProgressSnapshot:
    status: RunStatus
    planned: int
    conditions: tuple[ConditionResult, ...]
    figures: tuple[str, ...]
    current: str | None
    error: str | None

    @property
    def completed(self) -> int:
        return len(self.conditions)


class Progress:
    def __init__(self, conditions: tuple[str, ...]):
        plan = tuple(conditions)
        if not plan or any(not isinstance(name, str) or not name for name in plan):
            raise ValueError("experiment plan requires named conditions")
        if len(set(plan)) != len(plan):
            raise ValueError("experiment plan contains duplicate conditions")
        self._plan = plan
        self._status = RunStatus.RUNNING
        self._finished: dict[str, ConditionResult] = {}
        self._figures: tuple[str, ...] = ()
        self._current: str | None = None
        self._error: str | None = None

    @property
    def planned(self) -> int:
        return len(self._plan)

    @property
    def completed(self) -> int:
        return len(self._finished)

    def _require_running(self) -> None:
        if self._status != RunStatus.RUNNING:
            raise ValueError("experiment has already finished")

    def start(self, name: str) -> None:
        self._require_running()
        if name not in self._plan or name in self._finished:
            raise ValueError("condition is unknown or already completed")
        if self._current is not None and self._current not in self._finished:
            raise ValueError("current condition has not completed")
        self._current = name

    def complete(self, result: ConditionResult) -> None:
        self._require_running()
        if (
            self._current is None
            or result.condition != self._current
            or result.condition in self._finished
        ):
            raise ValueError("result must match the active condition")
        self._finished[result.condition] = result

    def succeed(self, figures: list[str]) -> None:
        self._require_running()
        if self.completed != self.planned:
            raise ValueError("all planned conditions must complete before success")
        self._figures = tuple(figures)
        self._status = RunStatus.SUCCEEDED
        self._current = None

    def fail(self, error: str) -> None:
        self._require_running()
        self._error = error
        self._status = RunStatus.FAILED

    def snapshot(self) -> ProgressSnapshot:
        return ProgressSnapshot(
            self._status,
            self.planned,
            tuple(self._finished.values()),
            self._figures,
            self._current,
            self._error,
        )
