"""Self-issued participant codes, persistent identity and restart-safe logs."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.application.study.runtime import token_key
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("participants") / "demo")


def app_for(bundle, database, monkeypatch, **kwargs):
    monkeypatch.setenv("MUSIC_RECSYS_RETENTION_DAYS", "90")  # Synthetic test policy only.
    return create_app(bundle, database, model_version="demo-itemcf", **kwargs)


def start(client, code=None):
    response = client.post(
        "/api/sessions",
        json={
            "persist": True,
            "consent_version": "local-ui-v4",
            **({"participant_code": code} if code else {}),
        },
    )
    response.raise_for_status()
    return response


def payload(app, cookie):
    return app.state.runtime.store.get(token_key(cookie), datetime.now(UTC))


def test_registration_requires_explicit_retention_and_consent(bundle, tmp_path, monkeypatch):
    monkeypatch.delenv("MUSIC_RECSYS_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("OTOLAB_RETENTION_DAYS", raising=False)
    app = create_app(bundle, tmp_path / "log.sqlite", model_version="demo-itemcf")
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        assert (
            client.post("/api/sessions", json={"consent_version": "local-ui-v4"}).status_code == 400
        )
        assert (
            client.post(
                "/api/sessions", json={"consent_version": "local-ui-v4", "persist": True}
            ).status_code
            == 400
        )
        with app.state.runtime.store.connect() as db:
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_restart_and_code_recover_original_session_and_events(bundle, tmp_path, monkeypatch):
    database = tmp_path / "log.sqlite"
    app = app_for(bundle, database, monkeypatch)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c)
        code, session_id = first.json()["participant_code"], first.json()["session_id"]
        cookie = c.cookies.get("music_session")
        original = payload(app, cookie)
        participant_id = original["session"]["participant_id"]
        expiry = original["session"]["retain_until"]
        assert (
            timedelta(days=89)
            < datetime.fromisoformat(expiry) - datetime.now(UTC)
            <= timedelta(days=90)
        )
        assert (
            "Max-Age=77759" in first.headers["set-cookie"]
        )  # Fixed 90-day deadline, no sliding extension.
        c.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
        c.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
        reply = c.post("/api/recommendations", json={"goal": "discover"}).json()
        item = reply["tracks"][0]["item_id"]
        playback = c.post(
            "/api/playbacks", json={"request_id": reply["request_id"], "item_id": item}
        ).json()
        event = dict(
            event_id="once",
            kind="play",
            request_id=reply["request_id"],
            item_id=item,
            playback_id=playback["playback_id"],
        )
        c.post("/api/events", json=event).raise_for_status()
        c.post(
            "/api/events", json={**event, "event_id": "like-once", "kind": "like"}
        ).raise_for_status()
        assert code not in database.read_bytes().decode(errors="ignore")
        assert cookie not in json.dumps(payload(app, cookie))
    # Restart more than 24 hours later using the same persisted database.
    future = datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setattr("src.application.study.runtime.now", lambda: future)
    monkeypatch.setattr("src.interfaces.http.server.now", lambda: future)
    app = app_for(bundle, database, monkeypatch)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        c.cookies.set("music_session", cookie)
        state = c.get("/api/session/state").json()
        assert state["session_id"] == session_id
        assert state["phase"] == "listen" and state["profile"]["items"] == ["sample-00"]
        assert state["recommendation"] == reply and state["actions"] == [f"like:{item}"]
        assert c.get(playback["url"]).status_code == 200
        c.post("/api/events", json=event).raise_for_status()
        assert len(c.get("/api/session/export").json()["events"]) == 2
        assert start(c).json()["session_id"] == session_id  # Double submission is idempotent.
    # A different browser supplies the code; the previous cookie is invalidated.
    with TestClient(app, headers={"X-Music-App": "1"}) as other:
        resumed = start(other, code).json()
        assert resumed["session_id"] == session_id and resumed["recommendation"] == reply
        new_cookie = other.cookies.get("music_session")
        data = payload(app, new_cookie)
        assert data["session"]["participant_id"] == participant_id
        assert data["session"]["retain_until"] == expiry
        assert app.state.runtime.store.get(token_key(cookie), future) is None
        assert "demo-itemcf" not in json.dumps(resumed)
        other.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
        other.post("/api/session/finish", json={}).raise_for_status()
        # Another visit receives a new session ID while retaining the participant/profile.
        next_session = start(other).json()
        assert next_session["session_id"] != session_id
        assert next_session["profile"]["items"] == ["sample-00"]
        assert (
            payload(app, other.cookies.get("music_session"))["session"]["participant_id"]
            == participant_id
        )
        with app.state.runtime.store.connect() as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


def test_rotate_logout_and_withdraw_all_sessions(bundle, tmp_path, monkeypatch):
    app = app_for(bundle, tmp_path / "log.sqlite", monkeypatch)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c).json()
        old_code = first["participant_code"]
        rotated = c.post("/api/participants/code").json()["participant_code"]
        assert c.post("/api/participants/check", json={"code": old_code}).status_code == 401
        assert (
            c.post(
                "/api/participants/check", json={"code": rotated.lower().replace("-", "")}
            ).status_code
            == 200
        )
        managed = app.state.runtime.store.export_session(first["session_id"], datetime.now(UTC))
        c.post("/api/session/logout").raise_for_status()
        assert not c.cookies.get("music_session")
        assert managed.exists()
        again = start(c, rotated).json()
        assert again["session_id"] != first["session_id"]
        c.delete("/api/session").raise_for_status()
        assert not managed.exists()
        assert c.post("/api/participants/check", json={"code": rotated}).status_code == 401
        with app.state.runtime.store.connect() as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0


def test_expiry_purges_identity_profile_and_logs(bundle, tmp_path, monkeypatch):
    database = tmp_path / "log.sqlite"
    app = app_for(bundle, database, monkeypatch)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c).json()
        managed = app.state.runtime.store.export_session(first["session_id"], datetime.now(UTC))
        future = datetime.now(UTC) + timedelta(days=91)
        monkeypatch.setattr("src.application.study.runtime.now", lambda: future)
        monkeypatch.setattr("src.interfaces.http.server.now", lambda: future)
        assert c.get("/api/session/state").status_code == 401
        assert not managed.exists()
        assert (
            c.post("/api/participants/check", json={"code": first["participant_code"]}).status_code
            == 401
        )
        with app.state.runtime.store.connect() as db:
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0


def test_changed_model_does_not_restore_old_assignment_or_delete_logs(
    bundle, tmp_path, monkeypatch
):
    database = tmp_path / "log.sqlite"
    app = app_for(bundle, database, monkeypatch)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        start(c).raise_for_status()
        cookie = c.cookies.get("music_session")
    other = create_app(bundle, database, model_version="demo-bpr")
    with TestClient(other, headers={"X-Music-App": "1"}) as c:
        c.cookies.set("music_session", cookie)
        assert c.get("/api/session/state").status_code == 401
        assert len(other.state.runtime.store.summaries(datetime.now(UTC))) == 1
        c.delete("/api/session").raise_for_status()
        assert other.state.runtime.store.summaries(datetime.now(UTC)) == []
