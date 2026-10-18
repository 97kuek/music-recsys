"""FastAPI connects the real local graph without making external SDK calls."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.application.study.sessions import token_key
from src.domain.recommendation.control import DecisionAnswer
from src.infrastructure.config.control import build_control
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


class Decisions:
    external = True

    def __init__(self):
        self.inputs = []
        self.before_call = lambda: None

    def choose(self, evidence, *, timeout_seconds):
        self.before_call()
        self.inputs.append(evidence)
        return DecisionAnswer("demo-itemcf", "test-local", "synthetic-model")


class Trace:
    external = True

    def __init__(self):
        self.rows = []

    def record(self, **row):
        self.rows.append(row)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("control-http") / "bundle")


@pytest.fixture
def configured(bundle, tmp_path):
    pytest.importorskip("langgraph")
    settings = tmp_path / "control.json"
    settings.write_text(
        json.dumps(
            {
                "targets": [
                    {"version": "demo-itemcf", "description": "synthetic local ItemCF"},
                    {"version": "demo-als", "description": "synthetic local ALS"},
                ],
                "feedback_choices": ["discover"],
                "budget": {"max_calls": 3, "timeout_seconds": 10.0},
            }
        )
    )
    provider, trace = Decisions(), Trace()
    database = tmp_path / "sessions.sqlite"

    def make():
        return create_app(
            bundle,
            database,
            audience="researcher",
            control_factory=lambda runtime: build_control(
                runtime, settings, provider=provider, trace=trace
            ),
        )

    return make, provider, trace


def start(client):
    client.post("/api/sessions", json={"persist": True}).raise_for_status()
    client.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()


def command(client, request_id, **fields):
    return client.post(
        "/api/control/recommendations",
        json={
            "request_id": request_id,
            "mode": "reactive",
            "top_k": 5,
            "decision_input": True,
            **fields,
        },
    )


def test_reactive_graph_http_lineage_idempotency_and_explicit_trace(configured):
    make, provider, trace = configured
    app = make()
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        start(client)

        def reserved_before_call():
            saved = app.state.runtime.sessions.store.get(
                token_key(client.cookies["music_session"]), datetime.now(UTC)
            )
            assert saved["control_states"]["reactive"]["in_flight"]

        provider.before_call = reserved_before_call
        first = command(client, "first")
        first.raise_for_status()
        assert first.json()["status"] == "ok"
        assert len(first.json()["recommendation"]["tracks"]) == 5
        assert command(client, "first").json() == first.json()
        assert len(provider.inputs) == 1 and not trace.rows
        assert (
            command(
                client, "bad-parent", explicit_adjustment=True, parent_request_id="wrong"
            ).status_code
            == 400
        )
        assert len(provider.inputs) == 1
        second = command(
            client,
            "second",
            explicit_adjustment=True,
            parent_request_id="first",
            trace_metadata=True,
            feedback_choices=["discover"],
        )
        second.raise_for_status()
        assert second.json()["audit"]["adjustment_index"] == 1
        assert provider.inputs[-1].previous_targets == ("demo-itemcf",)
        assert len(trace.rows) == 1 and set(trace.rows[0]["metadata"]) == {
            "mode",
            "status",
            "reason",
            "adjustment_index",
            "selection_seconds",
        }
        third = command(client, "third", explicit_adjustment=True, parent_request_id="second")
        third.raise_for_status()
        assert (
            command(
                client, "fourth", explicit_adjustment=True, parent_request_id="third"
            ).status_code
            == 400
        )
        assert len(provider.inputs) == 3
        client.delete("/api/session").raise_for_status()
        assert not app.state.runtime.control.controllers


def test_external_consent_and_free_text_are_separate_and_never_persisted(configured):
    make, provider, _ = configured
    app = make()
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        start(client)
        reply = command(client, "denied", decision_input=False)
        assert reply.json()["reason"] == "external_decision_consent_required"
        assert not provider.inputs
        text = "synthetic-private-feedback"
        blocked = command(client, "text-denied", feedback_text=text)
        assert blocked.json()["reason"] == "external_text_consent_required"
        assert not provider.inputs
        accepted = command(client, "text-ok", feedback_text=text, feedback_text_permission=True)
        assert accepted.json()["status"] == "ok"
        payload = app.state.runtime.sessions.store.get(
            token_key(client.cookies["music_session"]), datetime.now(UTC)
        )
        assert text not in json.dumps(payload)
        assert provider.inputs[-1].feedback.text == text


def test_restart_fails_closed_instead_of_resetting_calls(configured):
    make, provider, _ = configured
    with TestClient(make(), headers={"X-Music-App": "1"}) as client:
        start(client)
        command(client, "first").raise_for_status()
        cookie = client.cookies["music_session"]
    with TestClient(make(), headers={"X-Music-App": "1"}) as restarted:
        restarted.cookies.set("music_session", cookie)
        assert restarted.get("/api/session/state").status_code == 200
        reply = command(restarted, "second", explicit_adjustment=True, parent_request_id="first")
        assert reply.status_code == 400 and "restart" in reply.json()["detail"]
        assert len(provider.inputs) == 1


def test_fixed_graph_has_no_provider_and_route_is_not_public(bundle, tmp_path):
    pytest.importorskip("langgraph")
    app = create_app(
        bundle,
        tmp_path / "fixed.sqlite",
        audience="researcher",
        control_path="deploy/control.example.json",
    )
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        start(client)
        result = command(
            client, "fixed", mode="fixed", fixed_target="demo-itemcf", decision_input=False
        )
        assert result.json()["status"] == "ok"
        assert result.json()["audit"]["selection_usage"] is None
        assert command(client, "missing-model").status_code == 400
    public = create_app(bundle, tmp_path / "public.sqlite", model_version="demo-itemcf")
    with TestClient(public, headers={"X-Music-App": "1"}) as client:
        assert command(client, "hidden").status_code == 404
    with pytest.raises(ValueError, match="local researcher"):
        create_app(bundle, tmp_path / "invalid.sqlite", control_path="deploy/control.example.json")


def test_save_failure_after_decision_never_repeats_call(configured, monkeypatch):
    make, provider, _ = configured
    app = make()
    with TestClient(app, headers={"X-Music-App": "1"}, raise_server_exceptions=False) as client:
        start(client)
        store = app.state.runtime.sessions.store
        original = store.put

        def put(key, payload):
            if provider.inputs:
                raise OSError("synthetic persistence failure")
            return original(key, payload)

        monkeypatch.setattr(store, "put", put)
        assert command(client, "uncertain").status_code == 500
        assert len(provider.inputs) == 1
        monkeypatch.setattr(store, "put", original)
        assert command(client, "uncertain").status_code == 400
        assert command(client, "new-id").status_code == 400
        assert len(provider.inputs) == 1
