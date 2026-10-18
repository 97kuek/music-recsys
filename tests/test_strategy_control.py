"""Provider-free control tests; optional SDK tests use only in-process mock transports."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.application.recommendation import RecommendationService
from src.application.strategy_control import StrategyControl
from src.domain.recommendation.contracts import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendationUsage,
)
from src.domain.recommendation.control import (
    ControlBudget,
    ControlFeedback,
    DecisionAnswer,
    DecisionInput,
    ExternalPermission,
)
from src.domain.recommendation.models import ModelSpec
from src.domain.study.events import SessionLog, StudySession
from src.infrastructure.research.decision_providers import OpenAIDecisions, ScriptedDecisions


class Ranker:
    def recommend(self, request):
        return RecommendationResponse(request.request_id, request.candidate_ids[: request.top_k])


def request(identifier="r1"):
    return RecommendationRequest(identifier, "participant-private", (), ("track1", "track2"), 2)


def control(mode="reactive", provider=None, **options):
    service = RecommendationService(
        tuple(
            (ModelSpec(target, "fixture", "{}", "train", "catalog", "source", 0), Ranker())
            for target in ("a", "b")
        )
    )
    return StrategyControl(
        service,
        session_id="session-private",
        condition_id="condition",
        mode=mode,
        targets=(("a", "Target A"), ("b", "Target B")),
        feedback_choices=("different",),
        budget=options.pop("budget", ControlBudget(3, 5)),
        provider=provider or ScriptedDecisions(("a", "b", "a")),
        fixed_target="a",
        **options,
    )


def test_explicit_adjustments_and_per_condition_limit_reuse_session_log():
    now = datetime.now(UTC)
    log = SessionLog(
        StudySession(
            "session-private",
            "participant-private",
            "study",
            "v1",
            "within",
            "AB",
            1,
            "synthetic",
            now,
            now + timedelta(days=1),
        )
    )
    for condition in ("condition", "second"):
        runner = control()
        runner.condition_id = condition
        parent = None
        for index in range(3):
            rid = f"{condition}-{index}"
            step = runner.run(
                request(rid),
                explicit_adjustment=index > 0,
                parent_request_id=parent,
                log=log,
                now=now,
            )
            assert step.status == "ok"
            assert step.adjustment_index == index
            assert step.execution.selection.strategy_id == "reactive"
            parent = rid
        with pytest.raises(ValueError, match="two adjustments"):
            runner.run(request("over"), explicit_adjustment=True, parent_request_id=parent)
    assert [row["list_number"] for row in log.export(now=now)["requests"]] == [1, 2, 3] * 2
    assert control().run(request("new-session")).adjustment_index == 0


def test_no_proactive_adjustment_and_no_stale_parent_or_input_leak():
    runner = control()
    runner.run(request())
    with pytest.raises(ValueError, match="explicit"):
        runner.run(request("r2"))
    with pytest.raises(ValueError, match="latest parent"):
        runner.run(request("r2"), explicit_adjustment=True, parent_request_id="other")
    with pytest.raises(ValueError, match="inputs changed"):
        runner.run(
            replace(request("r2"), history=("track1",)),
            explicit_adjustment=True,
            parent_request_id="r1",
        )
    with pytest.raises(ValueError, match="unregistered choices"):
        runner.run(
            request("r2"),
            explicit_adjustment=True,
            parent_request_id="r1",
            feedback=ControlFeedback(("arbitrary",)),
        )


def test_single_selection_has_current_feedback_while_reactive_can_reference_own_prior_targets():
    for mode in ("llm_once", "reactive", "fixed"):
        provider = ScriptedDecisions(("a", "b"))
        runner = control(mode, provider)
        runner.run(request())
        runner.run(
            request("r2"),
            explicit_adjustment=True,
            parent_request_id="r1",
            feedback=ControlFeedback(("different",)),
        )
        if mode == "fixed":
            assert provider.inputs == []
        else:
            assert provider.inputs[1].feedback.choices == ("different",)
            assert provider.inputs[1].previous_targets == (("a",) if mode == "reactive" else ())
            assert "participant-private" not in str(provider.inputs)
            assert "track1" not in str(provider.inputs)


def test_external_permission_is_separate_and_failed_attempts_consume_budget():
    provider = ScriptedDecisions(("a", "b"))
    provider.external = True
    runner = control(provider=provider, budget=ControlBudget(1, 5))
    assert runner.run(request()).reason == "external_decision_consent_required"
    assert runner.calls == 0
    step = runner.run(
        request("text"),
        feedback=ControlFeedback(text="private"),
        permission=ExternalPermission(decision_input=True),
    )
    assert step.reason == "external_text_consent_required"
    assert runner.calls == 0
    assert (
        runner.run(request("allowed"), permission=ExternalPermission(decision_input=True)).status
        == "ok"
    )
    blocked = runner.run(
        request("over"),
        explicit_adjustment=True,
        parent_request_id="allowed",
        permission=ExternalPermission(decision_input=True),
    )
    assert blocked.reason == "call_budget_exhausted"


@pytest.mark.parametrize("failure", ["invalid", "refusal", "exception", "timeout"])
def test_failures_do_not_generate_lists_or_fabricate_unknown_usage(failure):
    class Provider:
        external = False

        def choose(self, evidence, *, timeout_seconds):
            if failure == "exception":
                raise RuntimeError("private-content-and-secret-token")
            if failure == "timeout":
                raise TimeoutError("private-content")
            return DecisionAnswer("unregistered", "fixture", None, refusal=failure == "refusal")

    runner = control(provider=Provider(), budget=ControlBudget(1, 5))
    step = runner.run(request())
    assert step.status == "error" and step.execution is None
    assert step.audit()["selection_usage"] is None
    assert "private-content" not in str(step.audit())
    assert runner.run(request("retry")).reason == "call_budget_exhausted"


def test_unknown_cost_stops_subsequent_decisions_when_operator_sets_cost_ceiling():
    runner = control(budget=ControlBudget(3, 5, max_cost_usd=1))
    runner.run(request())
    assert (
        runner.run(request("r2"), explicit_adjustment=True, parent_request_id="r1").reason
        == "cost_unmeasured"
    )


def test_measured_cost_ceiling_and_failed_provider_unknown_cost_stop_later_calls():
    class Provider:
        external = False

        def choose(self, evidence, *, timeout_seconds):
            return DecisionAnswer(
                "a", "fixture", None, RecommendationUsage(api_cost_usd=0.4, llm_calls=1)
            )

    runner = control(provider=Provider(), budget=ControlBudget(3, 5, max_cost_usd=0.5))
    runner.run(request())
    runner.run(request("r2"), explicit_adjustment=True, parent_request_id="r1")
    assert (
        runner.run(request("r3"), explicit_adjustment=True, parent_request_id="r2").reason
        == "cost_budget_exhausted"
    )

    class FailedProvider(Provider):
        def choose(self, evidence, *, timeout_seconds):
            raise RuntimeError("private")

    runner = control(provider=FailedProvider(), budget=ControlBudget(3, 5, max_cost_usd=0.5))
    assert runner.run(request()).status == "error"
    assert runner.run(request("r2")).reason == "cost_unmeasured"


@pytest.mark.parametrize("field", ["decision_input", "feedback_text", "trace_metadata"])
@pytest.mark.parametrize("value", ["false", 1, None])
def test_purpose_specific_permissions_require_actual_booleans(field, value):
    with pytest.raises(ValueError, match="explicit booleans"):
        ExternalPermission(**{field: value})


def test_log_participant_expiry_and_restored_state_are_checked_before_decision():
    now = datetime.now(UTC)
    log = SessionLog(
        StudySession(
            "session-private",
            "participant-private",
            "study",
            "v1",
            "within",
            "AB",
            1,
            "synthetic",
            now,
            now + timedelta(days=1),
        )
    )
    provider = ScriptedDecisions(("a", "b"))
    runner = control(provider=provider)
    with pytest.raises(ValueError, match="identity"):
        runner.run(replace(request(), user_id="other-participant"), log=log, now=now)
    with pytest.raises(ValueError):
        runner.run(request(), log=SessionLog(log.session), now=now + timedelta(days=2))
    assert provider.inputs == []
    runner.run(request(), log=log, now=now)
    restored = control(provider=provider)
    with pytest.raises(ValueError, match="safe resume"):
        restored.run(request("after-restart"), log=log, now=now)
    assert len(provider.inputs) == 1


def test_inference_errors_keep_measured_timing_and_do_not_advance_list_chain():
    class ErrorRanker:
        def recommend(self, request):
            return RecommendationResponse(request.request_id, (), "error", "fixture_failure")

    runner = control()
    runner.service = RecommendationService(
        ((ModelSpec("a", "fixture", "{}", "train", "catalog", "source", 0), ErrorRanker()),)
    )
    step = runner.run(request())
    assert step.status == "error" and step.reason == "recommendation_error"
    assert step.adjustment_index == 0
    assert step.audit()["inference_seconds"] >= 0
    assert step.execution.response.status == "error"


def test_trace_failure_is_separate_from_successful_recommendation():
    class FailedTrace:
        external = False

        def record(self, **kwargs):
            raise RuntimeError("secret")

    result = control(trace=FailedTrace()).run(request())
    assert result.status == "ok" and result.trace_status == "failed"
    assert "secret" not in str(result.audit())


def test_trace_is_optional_and_contains_only_allowlisted_metadata():
    class Trace:
        external = True
        calls = []

        def record(self, **kwargs):
            self.calls.append(kwargs)

    trace = Trace()
    runner = control(trace=trace)
    assert runner.run(request()).trace_status == "consent_required"
    assert trace.calls == []
    step = runner.run(
        request("r2"),
        explicit_adjustment=True,
        parent_request_id="r1",
        permission=ExternalPermission(trace_metadata=True),
    )
    assert step.trace_status == "recorded"
    encoded = json.dumps(trace.calls)
    assert "participant-private" not in encoded and "session-private" not in encoded
    assert "r2" not in encoded and "track1" not in encoded


def test_openai_sdk_actual_choice_schema_with_mock_transport_only():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx2")
    sent = []

    def handle(req):
        sent.append(json.loads(req.content))
        assert req.url.path == "/v1/decisions"
        return httpx.Response(
            200,
            json={
                "model": "operator-selected-model",
                "answers": [
                    {
                        "type": "choice",
                        "name": "recommendation_target",
                        "choice": "a",
                        "confidence": 0.8,
                        "probabilities": [{"value": "a", "probability": 1}],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 0,
                    "total_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    with openai.OpenAI(
        api_key="synthetic-key",
        base_url="http://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        provider = OpenAIDecisions(client, model="operator-selected-model")
        answer = provider.choose(
            DecisionInput(
                (("a", "Fixture A"),),
                "new",
                "initial_profile",
                2,
                10,
                5,
                ControlFeedback(("different",)),
            ),
            timeout_seconds=2,
        )
    assert answer.target_id == "a" and answer.usage.input_tokens == 12
    assert answer.usage.api_cost_usd is None
    assert sent[0]["questions"][0]["type"] == "choice"


def test_langgraph_real_local_execution_disables_ambient_tracing(monkeypatch):
    pytest.importorskip("langgraph")
    langsmith = pytest.importorskip("langsmith")
    from langsmith.run_helpers import get_tracing_context

    from src.infrastructure.research.control_graph import LangGraphControl

    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    class Provider(ScriptedDecisions):
        def choose(self, evidence, *, timeout_seconds):
            assert get_tracing_context()["enabled"] is False
            return super().choose(evidence, timeout_seconds=timeout_seconds)

    with langsmith.tracing_context(enabled=False):
        graph = LangGraphControl(control(provider=Provider(("a",))))
    step = graph.run(request())
    assert step.status == "ok" and step.execution.response.item_ids == ("track1", "track2")


def test_openai_sdk_failure_has_no_hidden_retry_or_content_in_audit():
    openai = pytest.importorskip("openai")
    httpx = pytest.importorskip("httpx2")
    sent = []

    def handle(req):
        sent.append(req)
        return httpx.Response(
            500,
            json={
                "error": {
                    "message": "private-feedback-secret",
                    "type": "server_error",
                }
            },
        )

    with openai.OpenAI(
        api_key="synthetic-key",
        base_url="http://fixture.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        runner = control(
            provider=OpenAIDecisions(client, model="operator-selected-model"),
            budget=ControlBudget(1, 5),
        )
        step = runner.run(request(), permission=ExternalPermission(decision_input=True))
    assert len(sent) == 1 and step.status == "error"
    assert step.selection_usage is None
    assert "private-feedback-secret" not in str(step.audit())


def test_langsmith_uses_explicit_client_and_rejects_extra_content():
    from src.infrastructure.research.control_traces import LangSmithControlTrace

    captured = []
    trace = LangSmithControlTrace(
        SimpleNamespace(create_run=lambda **kw: captured.append(kw)),
        project_name="synthetic-control",
    )
    step = control(trace=trace).run(request(), permission=ExternalPermission(trace_metadata=True))
    assert captured[0]["inputs"] == {} and str(captured[0]["id"]) == step.trace_id
    with pytest.raises(ValueError, match="sanitized"):
        trace.record(trace_id=step.trace_id, metadata={"feedback_text": "private"})


@pytest.mark.parametrize("engine", ["python", "langgraph"])
def test_synthetic_rehearsal_has_no_fabricated_ratings_or_cost(engine):
    if engine == "langgraph":
        pytest.importorskip("langgraph")
    from src.interfaces.cli.control_rehearsal import rehearse

    result = rehearse(engine=engine)
    assert result["synthetic"] is True and len(result["steps"]) == 9
    assert all(step["selection_usage"] is None for step in result["steps"])
    assert len(result["session"]["requests"]) == 9 and result["session"]["events"] == []
