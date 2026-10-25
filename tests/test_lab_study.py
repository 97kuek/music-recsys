import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from participant_support import start_session

from src.domain.study.events import InteractionEvent
from src.infrastructure.datasets.listening_data import file_sha256
from src.interfaces.cli.online_media import prepare
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app

pytestmark = pytest.mark.usefixtures("participant_retention")


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("lab-study") / "demo")


def test_profile_and_client_measurements_persist_only_with_consent(bundle, tmp_path):
    app = create_app(bundle, tmp_path / "log.sqlite", model_version="demo-itemcf")
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        start_session(client).raise_for_status()
        client.put("/api/profile", json={"items": ["sample-00"], "artists": []}).raise_for_status()
        reply = client.post("/api/recommendations", json={"goal": "discover"}).json()
        item = reply["tracks"][0]["item_id"]
        binding = client.post(
            "/api/playbacks", json={"request_id": reply["request_id"], "item_id": item}
        ).json()
        payload = {
            "event_id": "pause1",
            "request_id": reply["request_id"],
            "item_id": item,
            "kind": "pause",
            "playback_id": binding["playback_id"],
            "position_seconds": 20.0,
            "listened_seconds": 2.0,
        }
        client.post("/api/events", json=payload).raise_for_status()
        impression = {
            "event_id": "shown",
            "request_id": reply["request_id"],
            "item_id": item,
            "kind": "impression",
            "response_latency_ms": 10.0,
            "display_latency_ms": 25.0,
        }
        client.post("/api/events", json=impression).raise_for_status()
        with app.state.runtime.sessions.store.connect() as db:
            saved = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
        assert saved["initial_profile"]["selected_item_ids"] == ["sample-00"]
        assert saved["goals"][reply["request_id"]]["heard_item_ids"] == []
        assert saved["events"][0]["listened_seconds"] == 2.0
        assert saved["events"][1]["display_latency_ms"] == 25.0
        payload.update(event_id="bad", listened_seconds=-1.0)
        assert client.post("/api/events", json=payload).status_code == 422
        client.delete("/api/session").raise_for_status()
        with app.state.runtime.sessions.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_listening_measurement_requires_binding():
    event = InteractionEvent("e", "s", "r", "pause", datetime.now(UTC), item_id="i")
    with pytest.raises(ValueError, match="playback ID"):
        replace(event, listened_seconds=1.0)


def test_media_requires_scope_and_exact_unique_id(bundle, tmp_path):
    config = json.loads(bundle.read_text())
    config.update(mode="music4all", audio={})
    for entry in config["models"]:
        entry["path"] = str(bundle.parent / entry["path"])
    config["catalog"]["path"] = str(bundle.parent / "catalog.json")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    metadata = tmp_path / "metadata.csv"
    metadata.write_text("item_id,title,artists\nsample-00,Fixture,Fixture Artist\n")
    permission = tmp_path / "permission.json"
    raw = {
        "web_preview": False,
        "access_scope": "restricted-study",
        "evidence_ref": "test-fixture-only",
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "item_ids": ["sample-00"],
    }
    permission.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="permission"):
        prepare(path, metadata, bundle.parent, permission, tmp_path / "rejected")
    raw["web_preview"] = True
    permission.write_text(json.dumps(raw))
    prepared = prepare(path, metadata, bundle.parent, permission, tmp_path / "registered")
    new = json.loads(prepared.read_text())
    assert new["audio"]["sample-00"]["sha256"] == file_sha256(bundle.parent / "sample-00.wav")
    assert new["media_permission_sha256"] == file_sha256(permission)
    with pytest.raises(FileExistsError):
        prepare(path, metadata, bundle.parent, permission, prepared.parent)


def test_consent_version_and_optional_storage(bundle, tmp_path):
    # Legacy optional-storage contracts remain available only to the local researcher.
    app = create_app(
        bundle, tmp_path / "consent.sqlite", model_version="demo-itemcf", audience="researcher"
    )
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        client.post(
            "/api/sessions", json={"persist": True, "consent_version": "local-ui-v2"}
        ).raise_for_status()
        with app.state.runtime.sessions.store.connect() as db:
            saved = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
        assert saved["session"]["consent_version"] == "local-ui-v2"
        assert saved["session"]["started_at"]
        assert client.post("/api/sessions", json={"consent_version": "unknown"}).status_code == 422
        client.post(
            "/api/sessions", json={"persist": False, "consent_version": "local-ui-v2"}
        ).raise_for_status()
        with app.state.runtime.sessions.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_v3_managed_export_and_deletion_survive_restart(bundle, tmp_path):
    database = tmp_path / "sessions.sqlite"
    app = create_app(bundle, database, model_version="demo-itemcf", audience="researcher")
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        reply = client.post(
            "/api/sessions", json={"persist": True, "consent_version": "local-ui-v3"}
        )
        reply.raise_for_status()
        session_id = reply.json()["session_id"]
        token = client.cookies.get("music_session")
        store = app.state.runtime.sessions.store
        summaries = store.summaries(datetime.now(UTC))
        assert summaries[0]["session_id"] == session_id
        exported = store.export_session(session_id, datetime.now(UTC))
        payload = json.loads(exported.read_text())
        assert payload["payload"]["session"]["consent_version"] == "local-ui-v3"
        assert token not in exported.read_text()
        assert exported.stat().st_mode & 0o777 == 0o600
        with pytest.raises(FileExistsError):
            store.export_session(session_id, datetime.now(UTC))
        with pytest.raises(ValueError):
            store.export_session("../../outside", datetime.now(UTC))
    restarted = create_app(bundle, database, model_version="demo-itemcf", audience="researcher")
    with TestClient(restarted, headers={"X-Music-App": "1"}) as client:
        client.cookies.set("music_session", token)
        assert client.get("/api/session/state").status_code == 200
        client.delete("/api/session").raise_for_status()
        assert not exported.exists()
        assert restarted.state.runtime.sessions.store.summaries(datetime.now(UTC)) == []


def test_expiry_removes_managed_exports(bundle, tmp_path):
    app = create_app(bundle, tmp_path / "sessions.sqlite", model_version="demo-itemcf")
    runtime = app.state.runtime
    token, session = runtime.sessions.start(True, False, consent_version="local-ui-v3")
    store = runtime.sessions.store
    exported = store.export_session(session.log.session.session_id, datetime.now(UTC))
    store.purge(datetime.now(UTC) + timedelta(days=2))
    assert not exported.exists()
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_availability_uses_explicit_schedule(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIC_RECSYS_NEXT_OPEN_AT", "2030-01-02T10:00:00+09:00")
    app = create_app(bundle, tmp_path / "sessions.sqlite", model_version="demo-itemcf")
    with TestClient(app) as client:
        reply = client.get("/availability")
        assert reply.json()["state"] == "online"
        assert reply.json()["next_open_at"] == "2030-01-02T01:00:00+00:00"
        assert reply.headers["cache-control"] == "no-store"
    monkeypatch.setenv("MUSIC_RECSYS_NEXT_OPEN_AT", "2030-01-02T10:00:00")
    with pytest.raises(ValueError, match="timezone"):
        create_app(bundle, tmp_path / "invalid.sqlite", model_version="demo-itemcf")
