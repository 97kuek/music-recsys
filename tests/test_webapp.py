"""ローカルMVPのAPI、同意境界、再生と推薦の結合を検証する。"""

import json

import pytest
from fastapi.testclient import TestClient

from src.webapp.prepare import demo
from src.webapp.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("web") / "demo")


@pytest.fixture
def client(bundle, tmp_path):
    app = create_app(bundle, tmp_path / "logs.sqlite", audience="researcher")
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        yield client


def start(client, persist=False, allow_text=False):
    result = client.post("/api/sessions", json=dict(persist=persist, allow_text=allow_text))
    assert result.status_code == 200
    return result.json()


def recommend(client, version="demo-itemcf", goal="discover"):
    response = client.post(
        "/api/recommendations", json=dict(version=version, goal=goal, top_k=5, text="private note")
    )
    assert response.status_code == 200, response.text
    return response.json()


def event(client, result, kind, **extra):
    return client.post(
        "/api/events",
        json=dict(event_id=f"event-{kind}", request_id=result["request_id"], kind=kind, **extra),
    )


@pytest.mark.parametrize("version", ["demo-popularity", "demo-itemcf", "demo-als", "demo-bpr"])
def test_saved_models_and_profile(client, version, monkeypatch):
    # 推論APIから学習に戻らない。
    def forbidden(*args, **kwargs):
        raise AssertionError("request must not train")

    monkeypatch.setattr("src.recommenders.full_itemcf.FullItemCF.__init__", forbidden)
    start(client)
    assert client.put("/api/profile", json={"items": ["sample-00"]}).status_code == 200
    result = recommend(client, version)
    assert result["model_version"] == version
    assert len(result["tracks"]) == 5
    assert "sample-00" not in [track["item_id"] for track in result["tracks"]]
    assert result["latency_ms"] >= 0


def test_play_feedback_finish_delete(client):
    start(client, persist=True)
    result = recommend(client)
    item = result["tracks"][0]["item_id"]
    assert event(client, result, "play", item_id=item).status_code == 400
    playback = client.post(
        "/api/playbacks", json=dict(request_id=result["request_id"], item_id=item)
    ).json()
    assert client.get(playback["url"]).content.startswith(b"RIFF")
    for kind in ("play", "pause", "ended"):
        assert (
            event(
                client,
                result,
                kind,
                item_id=item,
                playback_id=playback["playback_id"],
                position_seconds=1.0,
            ).status_code
            == 200
        )
    assert event(client, result, "like", item_id=item).status_code == 200
    assert event(client, result, "like", item_id=item).status_code == 200  # 再送は冪等
    assert event(client, result, "rating", rating=4).status_code == 200
    repeated = recommend(client, goal="familiar")
    assert repeated["effective_task"] == "repeat"
    assert [x["item_id"] for x in repeated["tracks"]] == [item]
    payload = client.get("/api/session/export").json()
    assert "private note" not in json.dumps(payload)
    assert len(payload["events"]) == 5
    assert client.post("/api/session/finish", json={}).status_code == 200
    assert client.get(playback["url"]).status_code == 404
    assert client.post("/api/recommendations", json={"version": "demo-bpr"}).status_code == 400
    assert client.delete("/api/session").status_code == 200
    assert client.get("/api/session/export").status_code == 401
    with client.app.state.runtime.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "persist,allow_text", [(False, False), (False, True), (True, False), (True, True)]
)
def test_consent_and_text(client, persist, allow_text):
    start(client, persist, allow_text)
    recommend(client)
    payload = client.get("/api/session/export").json()
    assert ("private note" in json.dumps(payload)) == (persist and allow_text)
    with client.app.state.runtime.store.connect() as db:
        rows = db.execute("SELECT token_hash, payload FROM sessions").fetchall()
    assert len(rows) == int(persist)
    if rows:
        assert client.cookies["music_session"] not in str(rows)


def test_origin_and_session_isolation(client):
    assert (
        client.post(
            "/api/sessions", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    start(client)
    result = recommend(client)
    item = result["tracks"][0]["item_id"]
    playback = client.post(
        "/api/playbacks", json=dict(request_id=result["request_id"], item_id=item)
    ).json()
    # 再開始は旧セッションを削除する。
    start(client)
    assert client.get(playback["url"]).status_code == 404
    assert event(client, result, "like", item_id=item).status_code == 400
    assert client.put("/api/profile", json={"items": ["missing"]}).status_code == 400


def test_artifact_hash_checked(bundle, tmp_path):
    config = json.loads(bundle.read_text())
    config["models"][0]["sha256"] = "0" * 64
    config["models"][0]["path"] = str(bundle.parent / config["models"][0]["path"])
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="manifest"):
        create_app(path, tmp_path / "logs.sqlite")
