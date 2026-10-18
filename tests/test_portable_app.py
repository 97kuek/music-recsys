import json
import shutil
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from participant_support import invited_start, issue_code

from src.infrastructure.auth.access import AccessVerifier
from src.interfaces.cli.online_bundle import inventory, package, verify
from src.interfaces.cli.online_prepare import demo
from src.interfaces.http.server import create_app


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return demo(tmp_path_factory.mktemp("portable") / "source")


def test_portable_relocation_and_allowlisted_files(bundle, tmp_path):
    # 原版と無関係な秘密ファイル・学習入力を含めない
    (bundle.parent / "private-test.txt").write_text("must not travel")
    output = tmp_path / "portable"
    config = package(bundle, output, study_path="deploy/demo-study.json")
    assert not (output / "learning.parquet").exists()
    assert not (output / "private-test.txt").exists()
    assert not list(output.rglob("*.sqlite"))
    verify(output)
    original_app = create_app(bundle, tmp_path / "original.sqlite", model_version="demo-itemcf")
    relocated = tmp_path / "mac" / "bundle"
    relocated.parent.mkdir()
    shutil.move(output, relocated)
    new_app = create_app(
        relocated / config.name, tmp_path / "new.sqlite", model_version="demo-itemcf"
    )
    results = []
    for app in (original_app, new_app):
        with TestClient(app, headers={"X-Music-App": "1"}) as c:
            invited_start(c).raise_for_status()
            c.put("/api/profile", json={"items": ["sample-00"]}).raise_for_status()
            response = c.post("/api/recommendations", json={})
            response.raise_for_status()
            results.append([t["item_id"] for t in response.json()["tracks"]])
    assert results[0] == results[1]
    verify(relocated)
    (relocated / "study.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        verify(relocated)
    with pytest.raises(FileExistsError):
        package(bundle, relocated)


def test_corrupt_copy_is_not_published(bundle, tmp_path):
    source = tmp_path / "source"
    shutil.copytree(bundle.parent, source)
    config = json.loads((source / "config.json").read_text())
    (source / config["catalog"]["path"]).write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        package(source / "config.json", tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".bundle-*"))


def test_capacity_budget_is_checked_before_copy_and_before_publication(bundle, tmp_path):
    report = inventory(bundle)
    assert report["audio_count"] == 12
    assert report["payload_bytes"] == sum(report["bytes"].values())
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        package(bundle, output, max_bytes=1)
    assert not output.exists()
    # Payloadが収まっても生成JSONを含む完成物が超過したら公開しない
    with pytest.raises(ValueError, match="including generated JSON"):
        package(bundle, output, max_bytes=report["payload_bytes"])
    assert not output.exists()
    assert not list(tmp_path.glob(".bundle-*"))
    package(bundle, output, max_bytes=report["payload_bytes"] + 100_000)
    verify(output)
    assert sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) <= (
        report["payload_bytes"] + 100_000
    )


def test_access_verifies_signature_issuer_audience_expiry(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = AccessVerifier("otolab-test", "application-audience")
    monkeypatch.setattr(
        verifier.keys, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=key.public_key())
    )
    now = datetime.now(UTC)
    claims = {
        "iss": verifier.issuer,
        "aud": "application-audience",
        "sub": "test-user",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }

    def encode(value):
        return jwt.encode(value, key, algorithm="RS256")

    assert verifier.verify(encode(claims))
    for patch in (
        {"aud": "wrong"},
        {"iss": "https://wrong.example"},
        {"exp": now - timedelta(seconds=1)},
    ):
        assert not verifier.verify(encode({**claims, **patch}))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert not verifier.verify(jwt.encode(claims, other, algorithm="RS256"))
    assert not verifier.verify("")
    assert not verifier.verify("not-a-jwt")


def test_remote_auth_covers_ui_api_and_audio(bundle, tmp_path, monkeypatch):
    monkeypatch.setenv("OTOLAB_ACCESS_TEAM", "otolab-test")
    monkeypatch.setenv("OTOLAB_ACCESS_AUD", "application-audience")
    monkeypatch.setenv("OTOLAB_ALLOWED_ORIGINS", "https://otolab.example")
    monkeypatch.setenv("OTOLAB_ALLOWED_HOSTS", "otolab.example")
    # Token暗号検証は上のテスト。ここはHTTP認可境界を検証する
    monkeypatch.setattr(AccessVerifier, "verify", lambda self, token: token == "test-valid")
    app = create_app(bundle, tmp_path / "log.sqlite", model_version="demo-itemcf")
    with TestClient(app, base_url="https://otolab.example") as c:
        for path in ["/", "/api/status", "/api/audio/unknown", "/healthz"]:
            assert c.get(path).status_code == 401
        headers = {
            "Cf-Access-Jwt-Assertion": "test-valid",
            "X-Music-App": "1",
            "Origin": "https://otolab.example",
        }
        response = c.post(
            "/api/sessions",
            json={
                "persist": True,
                "consent_version": "local-ui-v4",
                "participant_code": issue_code(app),
            },
            headers=headers,
        )
        response.raise_for_status()
        assert "Secure" in response.headers["set-cookie"]
        headers["Origin"] = "https://untrusted.example"
        assert c.post("/api/sessions", json={}, headers=headers).status_code == 403
    with pytest.raises(ValueError, match="participant"):
        create_app(bundle, tmp_path / "other.sqlite", audience="researcher")
