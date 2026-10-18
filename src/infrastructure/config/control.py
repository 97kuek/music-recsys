"""Explicit local control configuration; secret values are read only at composition time."""

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.application.study.control import ResearchControlActions
from src.domain.recommendation.contracts import validate_ids
from src.domain.recommendation.control import ControlBudget, validate_targets


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Target(_Strict):
    version: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)


class _Budget(_Strict):
    max_calls: int = Field(ge=0, le=100)
    timeout_seconds: float = Field(gt=0, le=120, allow_inf_nan=False)
    max_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class _Decision(_Strict):
    model: str = Field(min_length=1)


class _Trace(_Strict):
    project: str = Field(min_length=1)
    api_url: str = Field(pattern=r"^https://")


class ControlConfig(_Strict):
    targets: list[_Target] = Field(min_length=1, max_length=20)
    feedback_choices: list[str] = Field(max_length=20)
    budget: _Budget
    decision: _Decision | None = None
    trace: _Trace | None = None


def build_control(runtime, path, *, provider=None, trace=None, runner_factory=None):
    content = Path(path).read_bytes()
    config = ControlConfig.model_validate(json.loads(content))
    targets = tuple((t.version, t.description) for t in config.targets)
    validate_targets(targets)
    validate_ids(tuple(config.feedback_choices))
    if config.decision is not None and provider is None:
        from src.infrastructure.research.decision_providers import openai_decisions

        key = os.environ.get("MUSIC_RECSYS_DECISION_API_KEY")
        if not key:
            raise ValueError("MUSIC_RECSYS_DECISION_API_KEY must be explicitly configured")
        provider = openai_decisions(
            model=config.decision.model, api_key=key, timeout_seconds=config.budget.timeout_seconds
        )
    if config.trace is not None and trace is None:
        from src.infrastructure.research.control_traces import langsmith_control_trace

        key = os.environ.get("MUSIC_RECSYS_LANGSMITH_API_KEY")
        if not key:
            raise ValueError("MUSIC_RECSYS_LANGSMITH_API_KEY must be explicitly configured")
        trace = langsmith_control_trace(
            api_key=key, api_url=config.trace.api_url, project_name=config.trace.project
        )
    if runner_factory is None:
        from src.infrastructure.research.control_graph import LangGraphControl

        runner_factory = LangGraphControl
    return ResearchControlActions(
        runtime.recommendations,
        runtime.sessions,
        targets=targets,
        feedback_choices=tuple(config.feedback_choices),
        budget=ControlBudget(**config.budget.model_dump()),
        runner_factory=runner_factory,
        config_key=hashlib.sha256(content).hexdigest(),
        provider=provider,
        trace=trace,
    )
