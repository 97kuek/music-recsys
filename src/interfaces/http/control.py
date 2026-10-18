"""Local researcher-only strategy control; no implicit external API calls."""

from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from src.domain.recommendation.control import ControlFeedback, ExternalPermission


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=100)
    mode: Literal["fixed", "llm_once", "reactive"]
    top_k: int = Field(ge=2, le=20)
    fixed_target: str | None = Field(default=None, min_length=1, max_length=200)
    explicit_adjustment: bool = False
    parent_request_id: str | None = Field(default=None, min_length=1, max_length=100)
    feedback_choices: list[str] = Field(default_factory=list, max_length=20)
    feedback_text: str | None = Field(default=None, max_length=2000)
    decision_input: bool = False
    feedback_text_permission: bool = False
    trace_metadata: bool = False


def register_control_routes(app, runtime, require_researcher, owner):
    @app.post("/api/control/recommendations")
    def recommend(body: ControlCommand, request: Request):
        require_researcher()
        if runtime.control is None:
            raise ValueError("研究用control設定を明示して起動してください")
        with runtime.lock:
            token, _ = owner(request)
            return runtime.control.run(
                token,
                request_id=body.request_id,
                mode=body.mode,
                top_k=body.top_k,
                fixed_target=body.fixed_target,
                explicit_adjustment=body.explicit_adjustment,
                parent_request_id=body.parent_request_id,
                feedback=ControlFeedback(tuple(body.feedback_choices), body.feedback_text),
                permission=ExternalPermission(
                    body.decision_input, body.feedback_text_permission, body.trace_metadata
                ),
            )
