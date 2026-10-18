"""Bounded strategy-control values, independent of API providers and orchestration SDKs."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Literal

from src.domain.recommendation.contracts import (
    RecommendationRequest,
    RecommendationUsage,
    validate_id,
    validate_ids,
)
from src.domain.recommendation.models import RecommendationExecution


@dataclass(frozen=True)
class ControlBudget:
    max_calls: int
    timeout_seconds: float
    max_cost_usd: float | None = None

    def __post_init__(self):
        if type(self.max_calls) is not int or self.max_calls < 0:
            raise ValueError("max_calls must be a nonnegative integer")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("a finite positive timeout is required")
        if self.max_cost_usd is not None and (
            type(self.max_cost_usd) not in (int, float)
            or not math.isfinite(self.max_cost_usd)
            or self.max_cost_usd < 0
        ):
            raise ValueError("cost ceiling must be finite and nonnegative")


@dataclass(frozen=True)
class ControlFeedback:
    choices: tuple[str, ...] = ()
    text: str | None = None

    def __post_init__(self):
        validate_ids(self.choices)
        if self.text is not None and (not isinstance(self.text, str) or len(self.text) > 2000):
            raise ValueError("feedback text must be at most 2000 characters")


@dataclass(frozen=True)
class DecisionInput:
    """No participant ID, session ID, raw history, track ID, or arbitrary request text."""

    targets: tuple[tuple[str, str], ...]
    task: str
    input_kind: str
    history_count: int
    candidate_count: int
    top_k: int
    feedback: ControlFeedback
    previous_targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionAnswer:
    target_id: str | None
    provider: str
    model: str | None
    usage: RecommendationUsage | None = None
    refusal: bool = False

    def __post_init__(self):
        validate_id(self.provider)
        if self.model is not None:
            validate_id(self.model)
        if self.target_id is not None:
            validate_id(self.target_id)
        if self.usage is not None and not isinstance(self.usage, RecommendationUsage):
            raise ValueError("decision usage must be measured RecommendationUsage or None")
        if type(self.refusal) is not bool:
            raise ValueError("refusal must be boolean")


@dataclass(frozen=True)
class ExternalPermission:
    """Separate purpose-specific permission; local profile consent does not imply this."""

    decision_input: bool = False
    feedback_text: bool = False
    trace_metadata: bool = False

    def __post_init__(self):
        if any(type(value) is not bool for value in asdict(self).values()):
            raise ValueError("external permission values must be explicit booleans")


@dataclass(frozen=True)
class ControlStep:
    trace_id: str
    session_id: str
    condition_id: str
    request_id: str
    parent_request_id: str | None
    adjustment_index: int
    mode: str
    status: Literal["ok", "blocked", "error"]
    reason: str
    selection_seconds: float
    provider: str | None = None
    model: str | None = None
    selection_usage: RecommendationUsage | None = None
    execution: RecommendationExecution | None = None
    trace_status: str = "disabled"

    def audit(self) -> dict:
        """Local linking record; content and credentials are deliberately omitted."""
        result = {
            key: value
            for key, value in asdict(self).items()
            if key not in {"execution", "selection_usage"}
        }
        result.update(
            format="music-recsys-control-step-v1",
            list_number=self.adjustment_index + 1,
            selection_usage=asdict(self.selection_usage) if self.selection_usage else None,
            selected_target=(self.execution.selection.model_version_id if self.execution else None),
            inference_seconds=self.execution.inference_seconds if self.execution else None,
            inference_usage=(
                asdict(self.execution.response.usage)
                if self.execution and self.execution.response.usage
                else None
            ),
        )
        return result


def frozen_input_digest(request: RecommendationRequest) -> str:
    """Request IDs vary per list; all recommendation inputs remain fixed per condition."""
    content = {
        "user_id": request.user_id,
        "history": request.history,
        "candidate_ids": request.candidate_ids,
        "top_k": request.top_k,
        "task": request.task,
        "input_kind": getattr(request, "input_kind", "listening_history"),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def validate_targets(targets: tuple[tuple[str, str], ...]) -> None:
    validate_ids(tuple(target for target, _ in targets))
    if not targets:
        raise ValueError("explicit registered targets are required")
    for _, description in targets:
        validate_id(description)
