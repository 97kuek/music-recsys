"""Explicit opt-in LangSmith metadata sink; raw SDK/graph tracing is never enabled."""

from datetime import UTC, datetime
from math import isfinite
from uuid import UUID


class LangSmithControlTrace:
    external = True

    def __init__(self, client, *, project_name: str):
        if not project_name.strip():
            raise ValueError("an explicit trace project is required")
        self.client, self.project_name = client, project_name

    def record(self, *, trace_id, metadata):
        if set(metadata) != {"mode", "status", "reason", "adjustment_index", "selection_seconds"}:
            raise ValueError("only sanitized control metadata is allowed")
        if (
            metadata["mode"] not in {"fixed", "llm_once", "reactive"}
            or metadata["status"] not in {"ok", "blocked", "error"}
            or metadata["reason"]
            not in {
                "completed",
                "timeout",
                "refusal",
                "invalid_target",
                "recommendation_error",
                "execution_failed",
                "external_decision_consent_required",
                "external_text_consent_required",
                "call_budget_exhausted",
                "cost_unmeasured",
                "cost_budget_exhausted",
            }
            or type(metadata["adjustment_index"]) is not int
            or not 0 <= metadata["adjustment_index"] <= 2
            or type(metadata["selection_seconds"]) not in (float, int)
            or not isfinite(metadata["selection_seconds"])
            or metadata["selection_seconds"] < 0
        ):
            raise ValueError("trace metadata must contain bounded control values")
        identifier = UUID(trace_id)
        self.client.create_run(
            id=identifier,
            name="recommendation-control",
            run_type="chain",
            inputs={},
            outputs=metadata,
            project_name=self.project_name,
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )


def langsmith_control_trace(*, api_key: str, api_url: str, project_name: str):
    """An operator must supply the endpoint and credentials; never reads ambient opt-ins."""
    from langsmith import Client

    return LangSmithControlTrace(
        Client(api_key=api_key, api_url=api_url, auto_batch_tracing=False),
        project_name=project_name,
    )
