"""Research-only strategy selection and explicit reactive adjustments; no HTTP/UI wiring."""

from dataclasses import replace
from time import perf_counter
from uuid import uuid4

from src.application.ports.control import ControlTrace, DecisionProvider
from src.application.recommendation import RecommendationService
from src.domain.recommendation.contracts import RecommendationRequest, validate_id, validate_ids
from src.domain.recommendation.control import (
    ControlBudget,
    ControlFeedback,
    ControlStep,
    DecisionAnswer,
    DecisionInput,
    ExternalPermission,
    frozen_input_digest,
    validate_targets,
)
from src.domain.recommendation.models import FixedStrategy, Selection
from src.domain.study.events import SessionLog


class StrategyControl:
    """One bounded selection per invocation, retaining only this condition's completed steps.

    This is an evaluation primitive, not an autonomous iterative agent. The operator
    must supply targets, feedback vocabulary and budgets. Failed attempts consume call
    budget, but do not create or advance an evaluated recommendation list.
    """

    def __init__(
        self,
        service: RecommendationService,
        *,
        session_id: str,
        condition_id: str,
        mode: str,
        targets: tuple[tuple[str, str], ...],
        feedback_choices: tuple[str, ...],
        budget: ControlBudget,
        provider: DecisionProvider | None = None,
        fixed_target: str | None = None,
        trace: ControlTrace | None = None,
    ):
        validate_id(session_id)
        validate_id(condition_id)
        validate_targets(targets)
        validate_ids(feedback_choices)
        if mode not in {"fixed", "llm_once", "reactive"}:
            raise ValueError("unsupported control mode")
        if mode == "fixed" and fixed_target not in dict(targets):
            raise ValueError("fixed control requires an explicitly allowed target")
        if mode != "fixed" and provider is None:
            raise ValueError("a decision provider is required")
        self.service, self.session_id, self.condition_id = service, session_id, condition_id
        self.mode, self.targets, self.feedback_choices = mode, targets, feedback_choices
        self.budget, self.provider, self.fixed_target = budget, provider, fixed_target
        self.trace = trace
        self.steps: list[ControlStep] = []
        self.calls = 0
        self._input_digest: str | None = None

    def run(
        self,
        request: RecommendationRequest,
        *,
        explicit_adjustment: bool = False,
        parent_request_id: str | None = None,
        feedback: ControlFeedback = ControlFeedback(),
        permission: ExternalPermission = ExternalPermission(),
        log: SessionLog | None = None,
        now=None,
    ) -> ControlStep:
        if type(explicit_adjustment) is not bool:
            raise ValueError("explicit_adjustment must be boolean")
        if not isinstance(permission, ExternalPermission):
            raise ValueError("purpose-specific external permissions are required")
        if log is not None and (
            log.session.session_id != self.session_id
            or log.session.participant_id != request.user_id
            or now is None
        ):
            raise ValueError("session log identity and timestamp must match")
        completed = [step for step in self.steps if step.status == "ok"]
        if log is not None:
            # Check expiry and restored-state consistency before any paid call.
            recorded = log.export(now=now)["requests"]
            if any(row["request_id"] == request.request_id for row in recorded):
                raise ValueError("request_id already recorded in this session")
            own_ids = [
                row["request_id"]
                for row in recorded
                if row.get("condition_id") == self.condition_id
            ]
            if own_ids != [step.request_id for step in completed]:
                raise ValueError("control state and session log differ; safe resume is required")
        if any(step.request_id == request.request_id for step in self.steps):
            raise ValueError("request_id already attempted")
        if completed:
            if not explicit_adjustment or parent_request_id != completed[-1].request_id:
                raise ValueError("adjustment needs an explicit request and the latest parent")
            if len(completed) >= 3:
                raise ValueError("at most two adjustments per session and condition")
        elif explicit_adjustment or parent_request_id:
            raise ValueError("initial recommendation cannot have an adjustment parent")
        if set(feedback.choices) - set(self.feedback_choices):
            raise ValueError("feedback contains unregistered choices")
        digest = frozen_input_digest(request)
        if self._input_digest is not None and self._input_digest != digest:
            raise ValueError("comparison inputs changed within a condition")
        self._input_digest = digest
        step = ControlStep(
            str(uuid4()),
            self.session_id,
            self.condition_id,
            request.request_id,
            parent_request_id,
            len(completed),
            self.mode,
            "blocked",
            "not_started",
            0,
        )
        reason = self._blocked(permission, feedback)
        if reason:
            return self._finish(replace(step, reason=reason), permission)
        started = perf_counter()
        answer = None
        try:
            if self.mode == "fixed":
                answer = DecisionAnswer(self.fixed_target, "fixed", None)
            else:
                self.calls += 1
                answer = self.provider.choose(
                    DecisionInput(
                        self.targets,
                        request.task,
                        getattr(request, "input_kind", "listening_history"),
                        len(request.history),
                        len(request.candidate_ids),
                        request.top_k,
                        feedback,
                        tuple(s.execution.model.version_id for s in completed)
                        if self.mode == "reactive"
                        else (),
                    ),
                    timeout_seconds=self.budget.timeout_seconds,
                )
            elapsed = perf_counter() - started
            step = replace(
                step,
                selection_seconds=elapsed,
                provider=answer.provider,
                model=answer.model,
                selection_usage=answer.usage,
            )
            if elapsed > self.budget.timeout_seconds:
                return self._finish(replace(step, status="error", reason="timeout"), permission)
            if answer.refusal:
                return self._finish(replace(step, status="error", reason="refusal"), permission)
            if answer.target_id not in dict(self.targets):
                return self._finish(
                    replace(step, status="error", reason="invalid_target"), permission
                )
            execution = self.service.recommend(request, FixedStrategy(answer.target_id))
            execution = replace(
                execution,
                selection=Selection(self.mode, "control-v1", answer.target_id),
                selection_seconds=elapsed + execution.selection_seconds,
            )
            if execution.response.status != "ok":
                return self._finish(
                    replace(
                        step, status="error", reason="recommendation_error", execution=execution
                    ),
                    permission,
                )
            if log is not None:
                log.record(
                    execution,
                    now=now,
                    condition_id=self.condition_id,
                    parent_request_id=parent_request_id,
                    adjustment_scope="condition",
                )
            return self._finish(
                replace(step, status="ok", reason="completed", execution=execution), permission
            )
        except Exception as exc:
            # Never expose provider exception strings: they can contain submitted content.
            reason = (
                "timeout"
                if isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError"
                else "execution_failed"
            )
            return self._finish(
                replace(
                    step,
                    status="error",
                    reason=reason,
                    selection_seconds=(
                        step.selection_seconds
                        if step.provider is not None
                        else perf_counter() - started
                    ),
                ),
                permission,
            )

    def _blocked(self, permission, feedback):
        if self.mode == "fixed":
            return None
        if self.provider.external and not permission.decision_input:
            return "external_decision_consent_required"
        if self.provider.external and feedback.text and not permission.feedback_text:
            return "external_text_consent_required"
        if self.calls >= self.budget.max_calls:
            return "call_budget_exhausted"
        if self.budget.max_cost_usd is not None and self.calls:
            costs = [
                s.selection_usage.api_cost_usd if s.selection_usage else None
                for s in self.steps
                if s.provider is not None
            ]
            if len(costs) < self.calls or any(cost is None for cost in costs):
                return "cost_unmeasured"
            if sum(costs) >= self.budget.max_cost_usd:
                return "cost_budget_exhausted"
        if self.budget.max_cost_usd == 0:
            return "cost_budget_exhausted"
        return None

    def _finish(self, step, permission):
        if self.trace is not None and (not self.trace.external or permission.trace_metadata):
            try:
                self.trace.record(
                    trace_id=step.trace_id,
                    metadata={
                        "mode": step.mode,
                        "status": step.status,
                        "reason": step.reason,
                        "adjustment_index": step.adjustment_index,
                        "selection_seconds": step.selection_seconds,
                    },
                )
                step = replace(step, trace_status="recorded")
            except Exception:
                step = replace(step, trace_status="failed")
        elif self.trace is not None:
            step = replace(step, trace_status="consent_required")
        self.steps.append(step)
        return step
