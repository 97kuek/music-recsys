"""Participation requires a locally issued identity, with short-code collision handling."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from participant_support import issue_code

from src.application.study.participants import Participants
from src.domain.study.participants import code_key
from src.infrastructure.persistence.sessions import LocalStore
from src.interfaces.cli.online_logs import main
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(
        demo(tmp_path / "bundle"), tmp_path / "log.sqlite", model_version="demo-itemcf"
    )


def test_public_session_cannot_issue_identity_or_bypass_consent(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        for body in ({}, {"persist": True}, {"persist": True, "consent_version": "local-ui-v4"}):
            assert client.post("/api/sessions", json=body).status_code in {400, 401}
        with app.state.runtime.sessions.store.connect() as db:
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0
        code = issue_code(app)
        body = {
            "persist": True,
            "consent_version": "local-ui-v6",
            "long_term_consent": True,
            "participant_code": code,
        }
        started = client.post("/api/sessions", json=body)
        started.raise_for_status()
        assert "participant_code" not in started.json()
        same = client.post(
            "/api/sessions", json={k: v for k, v in body.items() if k != "participant_code"}
        )
        assert same.json()["session_id"] == started.json()["session_id"]
        with app.state.runtime.sessions.store.connect() as db:
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 1


def test_failed_code_attempts_are_limited_across_check_and_session(app, monkeypatch):
    tick = [100.0]
    monkeypatch.setattr("src.interfaces.http.server.monotonic", lambda: tick[0])
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        for _ in range(10):
            assert client.post("/api/participants/check", json={"code": "bad"}).status_code == 401
        response = client.post(
            "/api/sessions", json={"persist": True, "consent_version": "local-ui-v4"}
        )
        assert response.status_code == 429 and response.headers["Retry-After"] == "60"
        assert client.get("/api/status").status_code == 200
        tick[0] += 61
        assert (
            client.post("/api/participants/check", json={"code": issue_code(app)}).status_code
            == 200
        )


def test_short_codes_retry_collisions_and_preserve_legacy_hashes(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    store = LocalStore(tmp_path / "log.sqlite")
    participants = Participants(store)
    codes = iter(["0007", "0007", "1234", "1234", "0007", "4567"])
    monkeypatch.setattr("src.application.study.participants.new_code", lambda: next(codes))
    first, code = participants.issue(expires_at=now + timedelta(days=1), now=now)
    second, other = participants.issue(expires_at=now + timedelta(days=1), now=now)
    assert (code, other) == ("0007", "1234")
    assert participants.rotate(second["participant_id"], now) == "4567"
    assert participants.resolve("0007", now)["participant_id"] == first["participant_id"]
    legacy = "ABCD-1234-5678-90AB-CDEF-1234"
    store.add_participant("legacy", code_key(legacy), now + timedelta(days=1))
    assert participants.resolve(legacy.lower(), now)["participant_id"] == "legacy"


def test_admin_cli_issues_five_codes_without_sessions(tmp_path, monkeypatch, capsys):
    import json

    path = tmp_path / "log.sqlite"
    store = LocalStore(path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "online_logs",
            "--database",
            str(path),
            "issue-codes",
            "--count",
            "5",
            "--retention-days",
            "7",
        ],
    )
    main()
    rows = json.loads(capsys.readouterr().out)
    assert len({row["participant_code"] for row in rows}) == 5
    assert all(
        len(row["participant_code"]) == 4 and row["participant_code"].isdigit() for row in rows
    )
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
