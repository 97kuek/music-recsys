"""累積試聴時間の保存と、再送・再開時の重複排除を確認する。"""

import json

import pytest
from fastapi.testclient import TestClient

from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("checkpoint") / "bundle")


def client_for(bundle, database):
    return TestClient(
        create_app(bundle, database, model_version="demo-itemcf"), headers={"X-Music-App": "1"}
    )


def setup(client):
    client.post("/api/sessions", json={"persist": True}).raise_for_status()
    client.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
    recommendation = client.post("/api/recommendations", json={}).json()
    item = recommendation["tracks"][0]["item_id"]
    binding = client.post(
        "/api/playbacks", json={"request_id": recommendation["request_id"], "item_id": item}
    ).json()
    return {
        "event_id": "checkpoint-1",
        "kind": "playback_progress",
        "item_id": item,
        "request_id": recommendation["request_id"],
        "playback_id": binding["playback_id"],
        "position_seconds": 3.5,
        "listened_seconds": 3.0,
    }


def test_checkpoint_is_saved_once_across_restart_and_finished_retry(bundle, tmp_path):
    database = tmp_path / "session.sqlite"
    with client_for(bundle, database) as client:
        payload = setup(client)
        client.post("/api/events", json=payload).raise_for_status()
        cookie = client.cookies["music_session"]
        first = client.get("/api/session/export").json()["events"][0]
    with client_for(bundle, database) as resumed:
        resumed.cookies.set("music_session", cookie)
        resumed.post("/api/events", json=payload).raise_for_status()
        resumed.post("/api/session/finish", json={}).raise_for_status()
        resumed.post("/api/events", json=payload).raise_for_status()
        events = resumed.get("/api/session/export").json()["events"]
        assert events == [first]
        assert events[0]["listened_seconds"] == 3.0
        assert (
            resumed.post("/api/events", json={**payload, "listened_seconds": 4.0}).status_code
            == 400
        )
        assert (
            resumed.post("/api/events", json={**payload, "event_id": "late-new"}).status_code == 400
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("playback_id", "other"),
        ("item_id", "sample-00"),
        ("listened_seconds", None),
        ("position_seconds", None),
        ("listened_seconds", -1),
    ],
)
def test_checkpoint_requires_binding_and_measured_time(bundle, tmp_path, field, value):
    with client_for(bundle, tmp_path / "session.sqlite") as client:
        payload = setup(client)
        assert client.post("/api/events", json={**payload, field: value}).status_code in (400, 422)
        assert client.get("/api/session/export").json()["events"] == []


def test_progress_snapshots_do_not_mark_an_item_heard_or_change_consent(bundle, tmp_path):
    with client_for(bundle, tmp_path / "session.sqlite") as client:
        payload = setup(client)
        for event_id, seconds in [("c1", 2.0), ("c2", 4.0)]:
            client.post(
                "/api/events", json={**payload, "event_id": event_id, "listened_seconds": seconds}
            ).raise_for_status()
        session = next(iter(client.app.state.runtime.sessions.active.values()))
        assert not session.heard
        export = client.get("/api/session/export").json()
        assert [e["listened_seconds"] for e in export["events"]] == [2.0, 4.0]
        assert max(e["listened_seconds"] for e in export["events"]) == 4.0
        assert session.log.session.consent_version == "local-save-v1"
        with client.app.state.runtime.sessions.store.connect() as db:
            saved = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
        assert saved["resume"]["format"] == "music-recsys-resume-v1"


def test_retry_is_acknowledged_only_after_sqlite_write_succeeds(bundle, tmp_path, monkeypatch):
    app = create_app(bundle, tmp_path / "session.sqlite", model_version="demo-itemcf")
    with TestClient(app, headers={"X-Music-App": "1"}, raise_server_exceptions=False) as client:
        payload = setup(client)
        store = app.state.runtime.sessions.store
        put = store.put
        failures = 0

        def fail_once(*args):
            nonlocal failures
            if failures == 0:
                failures += 1
                raise OSError("temporary write failure")
            return put(*args)

        monkeypatch.setattr(store, "put", fail_once)
        assert client.post("/api/events", json=payload).status_code == 500
        session = next(iter(app.state.runtime.sessions.active.values()))
        first = session.log.export(now=app.state.runtime.playback.clock())["events"][0]
        client.post("/api/events", json=payload).raise_for_status()
        with store.connect() as db:
            saved = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
        assert saved["events"] == [first]
