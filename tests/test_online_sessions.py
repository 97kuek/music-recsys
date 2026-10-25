"""Session feedback, consent, expiry and deletion on isolated synthetic databases."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.application.study.sessions import token_key
from src.domain.study.online_metrics import summarize_online
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("online-v2") / "bundle")


@pytest.fixture
def app(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIC_RECSYS_RETENTION_DAYS", "30")
    return create_app(bundle, tmp_path / "study.sqlite", model_version="demo-itemcf")


def start(client):
    return client.post(
        "/api/sessions",
        json={
            "consent_version": "local-ui-v8",
            "persist": True,
        },
    )


def recommend(client):
    client.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
    client.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
    response = client.post("/api/recommendations", json={"top_k": 5})
    response.raise_for_status()
    return response.json()


def emit(client, reply, kind, event_id, **fields):
    return client.post(
        "/api/events",
        json={
            "event_version": 2,
            "request_id": reply["request_id"],
            "kind": kind,
            "event_id": event_id,
            **fields,
        },
    )


def close_list(client, reply, **fields):
    client.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
    emit(client, reply, "evaluation_shown", "shown").raise_for_status()
    response = emit(
        client,
        reply,
        "list_evaluation",
        "evaluation",
        discovery_status="unanswered",
        discovery_item_ids=[],
        observation_complete=True,
        **fields,
    )
    response.raise_for_status()
    return response


def snapshot(app, client):
    return app.state.runtime.sessions.store.get(
        token_key(client.cookies.get("music_session")), datetime.now(UTC)
    )


def test_new_session_does_not_reuse_selections_likes_or_stored_preferences(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c).json()
        assert first["consent_version"] == "local-ui-v8"
        reply = recommend(c)
        emit(c, reply, "like", "liked", item_id=reply["tracks"][0]["item_id"]).raise_for_status()
        original = snapshot(app, c)
        assert "long_term_consent" not in original
        assert "profile_snapshot" not in original
        participant_id = original["session"]["participant_id"]
        # A legacy profile stays in the database, but is never read as recommendation input.
        legacy = json.dumps({"selected_item_ids": ["sample-05"], "derived_profile": {"old": True}})
        store = app.state.runtime.sessions.store
        with store.connect() as db:
            db.execute(
                "UPDATE participants SET profile=? WHERE participant_id=?", (legacy, participant_id)
            )
        close_list(c, reply)
        c.post("/api/session/finish", json={}).raise_for_status()
        second = start(c).json()
        assert second["session_id"] != first["session_id"]
        assert second["profile"] == {"items": [], "artists": []}
        current = snapshot(app, c)
        assert current["session"]["participant_id"] == participant_id
        assert current["session"]["retain_until"] == original["session"]["retain_until"]
        with store.connect() as db:
            assert (
                db.execute(
                    "SELECT profile FROM participants WHERE participant_id=?", (participant_id,)
                ).fetchone()[0]
                == legacy
            )
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


def test_retired_routes_and_new_consent_do_not_enable_profile_reuse(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        start(c).raise_for_status()
        assert c.get("/api/profile/long-term").status_code == 404
        assert c.put("/api/profile/long-term", json={"items": []}).status_code in {404, 405}
        assert c.put("/api/session/context", json={"choices": ["calmer"]}).status_code in {404, 405}
        assert (
            c.post(
                "/api/sessions",
                json={"persist": True, "consent_version": "local-ui-v8", "long_term_consent": True},
            ).status_code
            == 422
        )


def test_legacy_session_resumes_without_reactivating_retired_preferences(app, bundle, tmp_path):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c).json()
        recommend(c)
        stored = snapshot(app, c)
        stored["session"]["consent_version"] = "local-ui-v6"
        stored.update(
            long_term_consent=True, profile_snapshot={"inferred_tracks": [{"item_id": "sample-05"}]}
        )
        cookie = c.cookies.get("music_session")
        app.state.runtime.sessions.store.put(token_key(cookie), stored)
    restarted = create_app(bundle, tmp_path / "study.sqlite", model_version="demo-itemcf")
    with TestClient(restarted, headers={"X-Music-App": "1"}) as c:
        c.cookies.set("music_session", cookie)
        state = c.get("/api/session/state").json()
        assert state["session_id"] == first["session_id"]
        assert state["profile"]["items"] == ["sample-00"]
        assert "long_term_consent" not in state
        c.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
        resaved = snapshot(restarted, c)
        assert resaved["profile_snapshot"] == stored["profile_snapshot"]
        assert resaved["long_term_consent"] is True
        assert c.get("/api/profile/long-term").status_code == 404


def test_session_requests_remain_separate_and_require_text_consent(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        start(c).raise_for_status()
        sessions = app.state.runtime.sessions
        token = c.cookies.get("music_session")
        with pytest.raises(PermissionError):
            sessions.set_context(token, ["calmer"], "private request")
        sessions.set_context(token, ["calmer"], "")
        assert snapshot(app, c)["session_context"]["choices"] == ["calmer"]
        c.post("/api/session/logout").raise_for_status()
        start(c).raise_for_status()
        assert snapshot(app, c)["session_context"] == {}


def test_versioned_list_evaluation_validates_likes_and_survives_restart(app, bundle, tmp_path):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        start(c).raise_for_status()
        reply = recommend(c)
        first, second = [t["item_id"] for t in reply["tracks"][:2]]
        for n, track in enumerate(reply["tracks"]):
            emit(
                c, reply, "impression", f"impression-{n}", item_id=track["item_id"]
            ).raise_for_status()
        emit(c, reply, "like", "like", item_id=first).raise_for_status()
        emit(c, reply, "like", "like-repeat", item_id=first).raise_for_status()
        assert emit(c, reply, "rating", "song-rating", item_id=first, rating=2).status_code == 400
        invalid = dict(
            discovery_status="selected", discovery_item_ids=[second], observation_complete=True
        )
        assert emit(c, reply, "list_evaluation", "invalid", **invalid).status_code == 400
        c.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
        emit(c, reply, "evaluation_shown", "shown").raise_for_status()
        answer = dict(
            rating=4,
            discovery_status="selected",
            discovery_item_ids=[first],
            observation_complete=True,
        )
        emit(c, reply, "list_evaluation", "answer", **answer).raise_for_status()
        emit(c, reply, "list_evaluation", "answer", **answer).raise_for_status()
        assert emit(c, reply, "list_evaluation", "other-answer", **answer).status_code == 400
        assert emit(c, reply, "like", "late-like", item_id=second).status_code == 400
        stored = snapshot(app, c)
        assert stored["schema_version"] == 2
        row = summarize_online(stored)["lists"][0]
        assert row["like_rate"]["value"] == 0.2
        assert row["satisfaction"] == 4 and row["discovery_success_tracks"] == 1
        cookie = c.cookies.get("music_session")
    restarted = create_app(bundle, tmp_path / "study.sqlite", model_version="demo-itemcf")
    with TestClient(restarted, headers={"X-Music-App": "1"}) as c:
        c.cookies.set("music_session", cookie)
        state = c.get("/api/session/state").json()
        assert state["list_evaluation"]["rating"] == 4
        assert "demo-itemcf" not in json.dumps(state)


def test_switch_logs_are_bound_to_playback_and_destination(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        start(c).raise_for_status()
        reply = recommend(c)
        first, second = [t["item_id"] for t in reply["tracks"][:2]]
        playback = c.post(
            "/api/playbacks", json={"request_id": reply["request_id"], "item_id": first}
        ).json()["playback_id"]
        fields = dict(
            item_id=first,
            playback_id=playback,
            position_seconds=25.0,
            listened_seconds=9.9,
            movement="previous",
            target_item_id=second,
            was_playing=True,
        )
        emit(c, reply, "play", "play", item_id=first, playback_id=playback).raise_for_status()
        emit(c, reply, "track_switch", "switch", **fields).raise_for_status()
        assert (
            emit(
                c, reply, "track_switch", "bad-playback", **{**fields, "playback_id": "other"}
            ).status_code
            == 400
        )
        assert (
            emit(
                c, reply, "track_switch", "bad-target", **{**fields, "target_item_id": "sample-00"}
            ).status_code
            == 400
        )
        close_list(c, reply)
        row = summarize_online(snapshot(app, c))["lists"][0]
        assert row["track_switch_rate"]["value"] == 1
        assert row["early_skip_rate"]["value"] == 1
        assert row["movement_counts"]["previous"] == 1


def test_legacy_satisfaction_is_restored_without_asking_for_a_second_rating(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        c.post(
            "/api/sessions",
            json={
                "persist": True,
                "consent_version": "local-ui-v4",
            },
        ).raise_for_status()
        reply = recommend(c)
        c.post(
            "/api/events",
            json={
                "event_id": "legacy",
                "request_id": reply["request_id"],
                "kind": "rating",
                "rating": 4,
            },
        ).raise_for_status()
        answer = c.get("/api/session/state").json()["list_evaluation"]
        assert answer["rating"] == 4 and not answer["observation_complete"]
        assert (
            emit(
                c,
                reply,
                "list_evaluation",
                "second",
                rating=5,
                discovery_status="none",
                discovery_item_ids=[],
                observation_complete=True,
            ).status_code
            == 400
        )


def test_delete_cascades_and_stale_save_cannot_resurrect_sessions(app):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c).json()
        stale = snapshot(app, c)
        store = app.state.runtime.sessions.store
        exported = store.export_session(first["session_id"], datetime.now(UTC))
        c.delete("/api/session").raise_for_status()
        assert not exported.exists()
        with pytest.raises(PermissionError):
            store.put("stale", stale)
        assert c.get("/api/session/state").status_code == 401
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0


def test_expiry_removes_participants_sessions_and_exports(app, monkeypatch):
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        first = start(c).json()
        store = app.state.runtime.sessions.store
        exported = store.export_session(first["session_id"], datetime.now(UTC))
        future = datetime.now(UTC) + timedelta(days=31)
        monkeypatch.setattr("src.application.study.clock.utc_now", lambda: future)
        monkeypatch.setattr("src.interfaces.http.server.now", lambda: future)
        assert c.get("/api/session/state").status_code == 401
        assert not exported.exists()
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0


def test_within_subject_inputs_are_frozen_despite_likes(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIC_RECSYS_RETENTION_DAYS", "30")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            dict(
                protocol_id="synthetic",
                seed=42,
                top_k=5,
                conditions=[
                    dict(condition_id="a", version="demo-itemcf"),
                    dict(condition_id="b", version="demo-als"),
                ],
            )
        )
    )
    app = create_app(bundle, tmp_path / "frozen.sqlite", study_path=plan)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        start(c).raise_for_status()
        first = recommend(c)
        liked = first["tracks"][0]["item_id"]
        emit(c, first, "like", "like", item_id=liked).raise_for_status()
        assert c.put("/api/profile", json={"items": ["sample-01"]}).status_code == 400
        close_list(c, first)
        c.post("/api/session/next", json={"period": 1}).raise_for_status()
        c.post("/api/recommendations", json={}).raise_for_status()
        stored = snapshot(app, c)
        assert len(stored["requests"]) == 2
        assert {r["history_count"] for r in stored["requests"]} == {1}
        assert all(
            g["initial_profile"]["selected_item_ids"] == ["sample-00"]
            for g in stored["goals"].values()
        )
