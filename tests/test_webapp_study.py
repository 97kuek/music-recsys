import json

import pytest
from fastapi.testclient import TestClient
from participant_support import start_session

from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app

pytestmark = pytest.mark.usefixtures("participant_retention")


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("study") / "demo")


def test_blinded_within_subject_flow(bundle, tmp_path):
    plan = tmp_path / "study.json"
    plan.write_text(
        json.dumps(
            {
                "protocol_id": "demo-ux-only",
                "seed": 42,
                "top_k": 5,
                "conditions": [
                    {"condition_id": "A", "version": "demo-itemcf"},
                    {"condition_id": "B", "version": "demo-als"},
                ],
            }
        )
    )
    app = create_app(bundle, tmp_path / "log.sqlite", study_path=plan)
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        status = c.get("/api/status").json()
        assert "models" not in status and status["experience"] == "experiment"
        assert c.get("/api/comparison").status_code == 404
        assert c.get("/openapi.json").status_code == 404
        start_session(c).raise_for_status()
        c.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
        c.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
        assert c.post("/api/recommendations", json={"version": "demo-bpr"}).status_code == 400
        first = c.post("/api/recommendations", json={}).json()
        assert "model_version" not in first and "route" not in first and "latency_ms" not in first
        assert c.post("/api/recommendations", json={}).json() == first
        assert c.put("/api/profile", json={"items": []}).status_code == 400
        assert c.post("/api/session/next", json={"period": 1}).status_code == 400
        c.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
        c.post("/api/session/next", json={"period": 1}).raise_for_status()
        assert c.post("/api/session/next", json={"period": 1}).status_code == 400
        c.post("/api/recommendations", json={}).raise_for_status()
        c.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
        c.post("/api/session/finish", json={}).raise_for_status()
        public = c.get("/api/session/export").text
        assert "demo-als" not in public and "demo-itemcf" not in public
        with app.state.runtime.sessions.store.connect() as db:
            saved = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
        assert len(saved["requests"]) == 2
        assert {r["model"]["version_id"] for r in saved["requests"]} == {"demo-als", "demo-itemcf"}
        assert {g["period"] for g in saved["goals"].values()} == {1, 2}
        assert saved["plan_sha256"] and saved["funnel"]


def test_external_api_auth_cookie_and_origin(bundle, tmp_path, monkeypatch):
    token = "test-token-" + "x" * 40
    monkeypatch.setenv("OTOLAB_API_TOKEN", token)
    monkeypatch.setenv("OTOLAB_ALLOWED_ORIGINS", "https://otolab.example")
    monkeypatch.setenv("OTOLAB_ALLOWED_HOSTS", "api.example")
    app = create_app(bundle, tmp_path / "log.sqlite", model_version="demo-itemcf")
    with TestClient(app, base_url="https://api.example") as c:
        assert c.get("/api/status").status_code == 401
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Music-App": "1",
            "Origin": "https://otolab.example",
        }
        response = c.post(
            "/api/sessions",
            json={
                "persist": True,
                "consent_version": "local-ui-v4",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        headers["Origin"] = "https://other.example"
        assert c.post("/api/sessions", json={}, headers=headers).status_code == 403
    with pytest.raises(ValueError, match="participant"):
        create_app(bundle, tmp_path / "other.sqlite", audience="researcher")


def test_missing_real_study_models_fail_before_serving(bundle, tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "protocol_id": "not-ready",
                "seed": 1,
                "top_k": 5,
                "conditions": [
                    {"condition_id": "a", "version": "real-als"},
                    {"condition_id": "b", "version": "real-bpr"},
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="not ready"):
        create_app(bundle, tmp_path / "log.sqlite", study_path=path)


@pytest.mark.parametrize(
    ("version", "required_route", "items", "accepted"),
    [
        ("demo-itemcf", "itemcf_history", ["sample-00"], True),
        ("demo-itemcf", "itemcf_history", [], False),
        ("demo-als", "als_fold_in_binary_seeds", ["sample-00"], True),
        ("demo-bpr", "trained_user", ["sample-00"], False),
        ("demo-bpr", None, ["sample-00"], True),
    ],
)
def test_condition_enforces_actual_route(
    bundle, tmp_path, version, required_route, items, accepted
):
    plan = tmp_path / "study.json"
    plan.write_text(
        json.dumps(
            {
                "protocol_id": "route-check",
                "seed": 42,
                "top_k": 5,
                "conditions": [
                    {"condition_id": name, "version": version, "required_route": required_route}
                    for name in ("a", "b")
                ],
            }
        )
    )
    app = create_app(bundle, tmp_path / "log.sqlite", study_path=plan)
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        start_session(client).raise_for_status()
        client.put("/api/profile", json={"items": items}).raise_for_status()
        client.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
        response = client.post("/api/recommendations", json={})
        assert response.status_code == (200 if accepted else 400), response.text
        with app.state.runtime.sessions.store.connect() as db:
            saved = json.loads(db.execute("SELECT payload FROM sessions").fetchone()[0])
        assert len(saved["requests"]) == int(accepted)
        assert len(saved["goals"]) == int(accepted)
        if not accepted:
            assert all(
                not s.recommendations and not s.replies
                for s in app.state.runtime.sessions.active.values()
            )


def test_profile_preview_is_private_and_not_a_recommendation(bundle, tmp_path):
    app = create_app(bundle, tmp_path / "preview.sqlite", model_version="demo-itemcf")
    with TestClient(app, headers={"X-Music-App": "1"}) as c:
        assert c.post("/api/previews", json={"item_id": "sample-00"}).status_code == 401
        start_session(c).raise_for_status()
        reply = c.post("/api/previews", json={"item_id": "sample-00"})
        reply.raise_for_status()
        url = reply.json()["url"]
        assert c.get(url).status_code == 200
        session = next(iter(app.state.runtime.sessions.active.values()))
        assert not session.heard and not session.recommendations and not session.playback_bindings
        assert not c.get("/api/session/export").json()["events"]
        with TestClient(app, headers={"X-Music-App": "1"}) as other:
            start_session(other).raise_for_status()
            assert other.get(url).status_code == 404
        c.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
        assert c.get(url).status_code == 404
        assert c.post("/api/previews", json={"item_id": "sample-00"}).status_code == 400
        c.delete("/api/session").raise_for_status()
        assert c.get(url).status_code == 401
