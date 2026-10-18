"""明示指定した予備実験計画。モデル選定や実験実施の承認を自動化しない。"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.domain.study.plan import Condition, StudyPlan


class ConditionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    condition_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=200)
    offline_run_ref: str | None = None
    controller: Literal["fixed", "llm_single", "reactive_agent"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    required_route: (
        Literal[
            "popularity",
            "itemcf_history",
            "trained_user",
            "als_fold_in_binary_seeds",
            "popularity_fallback",
        ]
        | None
    ) = None


class StudyPlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_id: str = Field(min_length=1, max_length=100)
    seed: int
    top_k: int = Field(ge=2, le=20)
    conditions: list[ConditionPayload] = Field(min_length=2, max_length=8)
    design: Literal["exploratory_standard", "exploratory_strategy"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def read_study_plan(path) -> StudyPlan:
    payload = StudyPlanPayload.model_validate(json.loads(Path(path).read_text()))
    return StudyPlan(
        payload.protocol_id,
        payload.seed,
        payload.top_k,
        tuple(Condition(**condition.model_dump()) for condition in payload.conditions),
        payload.design,
    )
