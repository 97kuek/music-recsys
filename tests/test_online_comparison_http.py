"""Real application flow with isolated demo artifacts; participant/researcher isolation."""

import json

import pytest
from fastapi.testclient import TestClient
from participant_support import issue_code

from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("comparison") / "bundle")


def study_file(tmp_path):
    path = tmp_path / "study.json"
    path.write_text(
        json.dumps(
            dict(
                protocol_id="five-person-synthetic-test",
                seed=42,
                top_k=5,
                design="exploratory_standard",
                conditions=[
                    dict(condition_id=c, version=v)
                    for c, v in zip("ABC", ("demo-itemcf", "demo-als", "demo-popularity"))
                ],
            )
        )
    )
    return path


def test_assignment_restart_frozen_profile_analysis_and_deletion(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIC_RECSYS_RETENTION_DAYS", "2")
    path = study_file(tmp_path)
    database = tmp_path / "sessions.sqlite"
    app = create_app(bundle, database, study_path=path)
    code = issue_code(app, 2)
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        start = client.post(
            "/api/sessions",
            json=dict(
                persist=True,
                consent_version="local-ui-v5",
                long_term_consent=True,
                participant_code=code,
            ),
        )
        start.raise_for_status()
        client.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
        client.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
        first_request = None
        for period in range(3):
            reply = client.post("/api/recommendations", json={})
            reply.raise_for_status()
            rid = reply.json()["request_id"]
            first_request = first_request or rid
            assert len(reply.json()["tracks"]) == 5
            assert "model_version" not in reply.text and "condition_id" not in reply.text
            item = reply.json()["tracks"][0]["item_id"]
            client.post(
                "/api/events",
                json=dict(event_id=f"like-{period}", request_id=rid, kind="like", item_id=item),
            ).raise_for_status()
            client.post("/api/session/progress", json={"phase": "evaluate"}).raise_for_status()
            client.post(
                "/api/events",
                json=dict(
                    event_id=f"rating-{period}",
                    event_version=2,
                    request_id=rid,
                    kind="list_evaluation",
                    rating=period + 2,
                    observation_complete=True,
                    discovery_status="unanswered",
                ),
            ).raise_for_status()
            if period < 2:
                client.post("/api/session/next", json={"period": period + 1}).raise_for_status()
        client.post("/api/session/finish", json={}).raise_for_status()
        assert client.get("/api/online-analysis").status_code == 404
        assert client.get("/api/online-analysis/export").status_code == 404
        saved = app.state.runtime.sessions.store.analysis_snapshot(
            app.state.runtime.sessions.clock()
        )[0][0]
        assignment = saved["assignment"]
        assert (
            len({json.dumps(g["initial_profile"], sort_keys=True) for g in saved["goals"].values()})
            == 1
        )
    restarted = create_app(bundle, database, study_path=path)
    with TestClient(restarted, headers={"X-Music-App": "1"}) as client:
        denied = client.post(
            "/api/sessions",
            json=dict(
                persist=True,
                consent_version="local-ui-v5",
                long_term_consent=False,
                participant_code=code,
            ),
        )
        assert denied.status_code == 400
        new = client.post(
            "/api/sessions",
            json=dict(
                persist=True,
                consent_version="local-ui-v5",
                long_term_consent=True,
                participant_code=code,
            ),
        )
        new.raise_for_status()
        assert new.json()["profile_locked"]
        assert new.json()["profile"]["items"] == ["sample-00"]
        assert client.put("/api/profile", json={"items": ["sample-01"]}).status_code == 400
        current = next(iter(restarted.state.runtime.sessions.active.values()))
        assert current.order == assignment["order"]
        assert current.assignment["slot"] == assignment["slot"]
        researcher = create_app(bundle, database, audience="researcher")
        with TestClient(researcher) as admin:
            result = admin.get("/api/online-analysis")
            result.raise_for_status()
            assert result.headers["cache-control"] == "no-store"
            report = result.json()["experiments"][0]
            assert report["participants"] == 1 and report["session_count"] == 2
            assert first_request in {r["request_id"] for r in report["lists"]}
            assert "code_hash" not in result.text and code not in result.text
            assert "selected_item_ids" not in result.text
            for table in ("lists", "assignments", "paired_differences", "outcomes"):
                exported = admin.get(f"/api/online-analysis/export?format=csv&table={table}")
                exported.raise_for_status()
                assert "attachment" in exported.headers["content-disposition"]
            assert (
                admin.get("/api/online-analysis/export?format=csv&table=unknown").status_code == 400
            )
            client.delete("/api/session").raise_for_status()
            assert admin.get("/api/online-analysis").json()["experiments"] == []


def test_future_controllers_cannot_be_exposed_as_working_engines(bundle, tmp_path):
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps(
            dict(
                protocol_id="future",
                seed=1,
                top_k=5,
                design="exploratory_strategy",
                conditions=[
                    dict(condition_id=c, version="demo-itemcf", controller=controller)
                    for c, controller in (("A", "llm_single"), ("B", "reactive_agent"))
                ],
            )
        )
    )
    with pytest.raises(ValueError, match="not implemented"):
        create_app(bundle, tmp_path / "db.sqlite", study_path=path)
