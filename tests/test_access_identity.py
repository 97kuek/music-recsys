"""Signed Access identity, cookie ownership and automatic pseudonymous enrollment."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from src.application.study.sessions import token_key
from src.infrastructure.auth.access import AccessVerifier
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("access-identity") / "bundle")


@pytest.fixture
def authentication(monkeypatch):
    monkeypatch.setenv("MUSIC_RECSYS_ACCESS_TEAM", "identity-test")
    monkeypatch.setenv("MUSIC_RECSYS_ACCESS_AUD", "test-audience")
    monkeypatch.setenv("MUSIC_RECSYS_ALLOWED_HOSTS", "music.example")
    monkeypatch.setenv("MUSIC_RECSYS_ALLOWED_ORIGINS", "https://music.example")
    monkeypatch.setenv("MUSIC_RECSYS_RETENTION_DAYS", "1")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        jwt.PyJWKClient,
        "get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=key.public_key()),
    )

    def headers(subject="subject-a", **patch):
        now = datetime.now(UTC)
        claims = dict(
            iss="https://identity-test.cloudflareaccess.com",
            aud="test-audience",
            sub=subject,
            email="private-address@example.test",
            iat=now,
            exp=now + timedelta(minutes=5),
        )
        claims.update(patch)
        return {
            "Cf-Access-Jwt-Assertion": jwt.encode(claims, key, algorithm="RS256"),
            "X-Music-App": "1",
            "Origin": "https://music.example",
        }

    return headers


def open_session(client):
    response = client.post(
        "/api/sessions", json={"persist": True, "consent_version": "local-ui-v8"}
    )
    response.raise_for_status()
    return response.json()


def saved(app, client):
    cookie = next(c.value for c in client.cookies.jar if c.name == "music_session")
    return app.state.runtime.sessions.store.get(token_key(cookie), datetime.now(UTC))


def test_verified_identity_resumes_across_browsers_without_email_in_logs(
    bundle, tmp_path, authentication
):
    database = tmp_path / "sessions.sqlite"
    app = create_app(bundle, database, model_version="demo-itemcf")
    with TestClient(app, base_url="https://music.example", headers=authentication()) as first:
        initial = open_session(first)
        cookie = first.cookies.get("music_session")
        first.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
        first.post("/api/session/progress", json={"phase": "listen"}).raise_for_status()
        reply = first.post("/api/recommendations", json={}).json()
        payload = saved(app, first)
        participant = payload["session"]["participant_id"]
        expiry = payload["session"]["retain_until"]
    restarted = create_app(bundle, database, model_version="demo-itemcf")
    with TestClient(
        restarted, base_url="https://music.example", headers=authentication()
    ) as second:
        recovered = open_session(second)
        assert recovered["session_id"] == initial["session_id"]
        assert recovered["profile"]["items"] == ["sample-00"]
        assert recovered["recommendation"] == reply
        assert "participant_code" not in recovered
        stored = saved(restarted, second)
        assert stored["session"]["participant_id"] == participant
        assert stored["session"]["retain_until"] == expiry
        assert (
            restarted.state.runtime.sessions.store.get(token_key(cookie), datetime.now(UTC)) is None
        )
        with restarted.state.runtime.sessions.store.connect() as db:
            raw = json.dumps(list(db.iterdump()))
            assert "private-address@example.test" not in raw
            assert "subject-a" not in raw
        assert "private-address@example.test" not in json.dumps(stored)


def test_other_access_user_cannot_use_or_rebind_an_existing_cookie(
    bundle, tmp_path, authentication
):
    app = create_app(bundle, tmp_path / "sessions.sqlite", model_version="demo-itemcf")
    with TestClient(app, base_url="https://music.example", headers=authentication()) as first:
        initial = open_session(first)
        cookie = first.cookies.get("music_session")
        with TestClient(
            app, base_url="https://music.example", headers=authentication("subject-b")
        ) as other:
            other.cookies.set("music_session", cookie)
            assert other.get("/api/session/state").status_code == 401
            assert other.get("/api/session/export").status_code == 401
            assert other.delete("/api/session").status_code == 401
            isolated = open_session(other)
            assert isolated["session_id"] != initial["session_id"]
        assert first.get("/api/session/state").json()["session_id"] == initial["session_id"]


@pytest.mark.parametrize(
    "patch", [{"sub": ""}, {"aud": "wrong"}, {"iss": "https://wrong.example"}, {"exp": 1}]
)
def test_invalid_claims_do_not_enroll_or_expose_data(bundle, tmp_path, authentication, patch):
    app = create_app(bundle, tmp_path / "sessions.sqlite", model_version="demo-itemcf")
    with TestClient(
        app, base_url="https://music.example", headers=authentication(**patch)
    ) as client:
        assert client.get("/api/status").status_code == 401
        assert (
            client.post(
                "/api/sessions", json={"persist": True, "consent_version": "local-ui-v8"}
            ).status_code
            == 401
        )
    with app.state.runtime.sessions.store.connect() as db:
        assert db.execute("SELECT count(*) FROM participants").fetchone()[0] == 0


def test_valid_legacy_cookie_can_bind_once_without_changing_deadline(
    bundle, tmp_path, monkeypatch, authentication
):
    database = tmp_path / "sessions.sqlite"
    # Build a local legacy session without the external authentication boundary.
    for name in ("ACCESS_TEAM", "ACCESS_AUD", "ALLOWED_HOSTS", "ALLOWED_ORIGINS"):
        monkeypatch.delenv("MUSIC_RECSYS_" + name)
    local = create_app(bundle, database, model_version="demo-itemcf")
    with TestClient(local, headers={"X-Music-App": "1"}) as client:
        initial = open_session(client)
        cookie = client.cookies.get("music_session")
        original = saved(local, client)
        participant_id = original["session"]["participant_id"]
        with local.state.runtime.sessions.store.connect() as db:
            db.execute(
                "UPDATE participants SET code_hash=? WHERE participant_id=?",
                ("legacy-code-digest", participant_id),
            )
    monkeypatch.setenv("MUSIC_RECSYS_ACCESS_TEAM", "identity-test")
    monkeypatch.setenv("MUSIC_RECSYS_ACCESS_AUD", "test-audience")
    monkeypatch.setenv("MUSIC_RECSYS_ALLOWED_HOSTS", "music.example")
    monkeypatch.setenv("MUSIC_RECSYS_ALLOWED_ORIGINS", "https://music.example")
    remote = create_app(bundle, database, model_version="demo-itemcf")
    with TestClient(remote, base_url="https://music.example", headers=authentication()) as client:
        client.cookies.set("music_session", cookie)
        state = client.get("/api/session/state")
        state.raise_for_status()
        assert state.json()["session_id"] == initial["session_id"]
        stored = saved(remote, client)
        assert stored["session"]["retain_until"] == original["session"]["retain_until"]
        assert stored["session"]["participant_id"] == participant_id


def test_identity_is_not_accepted_from_an_unsigned_email_header(bundle, tmp_path, authentication):
    app = create_app(bundle, tmp_path / "sessions.sqlite", model_version="demo-itemcf")
    with TestClient(
        app,
        base_url="https://music.example",
        headers={
            "Cf-Access-Authenticated-User-Email": "private-address@example.test",
            "X-Music-App": "1",
        },
    ) as client:
        assert client.get("/api/status").status_code == 401
    verifier = AccessVerifier("identity-test", "test-audience")
    assert verifier.identity("not-a-jwt") is None


def test_code_entry_endpoints_are_removed(bundle, tmp_path, authentication):
    app = create_app(bundle, tmp_path / "sessions.sqlite", model_version="demo-itemcf")
    with TestClient(app, base_url="https://music.example", headers=authentication()) as client:
        open_session(client)
        for path in ("/api/participants/check", "/api/participants/code"):
            assert client.post(path, json={"code": "1234"}).status_code in {404, 405}
