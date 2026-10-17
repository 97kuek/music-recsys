import json
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from src.domain.recommendation.models import ModelSpec
from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.media.jamendo import JamendoClient, JamendoError, match_record, public_url
from src.interfaces.http.server import create_app


def transport(rows, status="success", code=0):
    return httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"headers": {"status": status, "code": code}, "results": rows}
        )
    )


def track(track_id="2", **extra):
    return {
        "id": track_id,
        "name": "Track " + track_id,
        "artist_name": "Artist",
        "audio": "https://mp3d.jamendo.com/test.mp3",
        "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
        **extra,
    }


def test_api_errors_and_secret_redaction():
    with pytest.raises(JamendoError, match="not_configured"):
        JamendoClient("").tracks(["2"])
    with pytest.raises(JamendoError, match="application_suspended"):
        JamendoClient("private", transport=transport([], "failed", 11)).tracks(["2"])

    def rate(request):
        return httpx.Response(429)

    with pytest.raises(JamendoError, match="rate_limited"):
        JamendoClient("private", transport=httpx.MockTransport(rate)).tracks(["2"])
    row = JamendoClient(
        "private", transport=transport([track(audio="https://mp3d.jamendo.com/private.mp3")])
    ).tracks(["2"])["2"]
    assert row["audio"] is None


def test_url_and_identity_guards():
    assert public_url("https://jamendo.com.evil.org/a") is None
    assert public_url("http://mp3d.jamendo.com/a") is None
    assert public_url("https://mp3d.jamendo.com/a?client_id=secret") is None
    expected = {"source_id": "2", "title": "Track 2", "artist": "Artist"}
    assert match_record(expected, track()) == "id_metadata_consistent"
    assert match_record(expected, track(name="Track 2 (Live)")) == "metadata_conflict"
    assert match_record(expected, None) == "not_returned"


def bundle(tmp_path):
    records = [
        {
            "item_id": "mtg:" + i,
            "source_id": i,
            "title": "Track " + i,
            "artist": "Artist",
            "artists": ["Artist"],
        }
        for i in ("1", "2")
    ]
    (tmp_path / "catalog.json").write_text(json.dumps(records))
    (tmp_path / "features.json").write_text(
        json.dumps({r["item_id"]: {"rock": 1} for r in records})
    )
    sha = file_sha256(tmp_path / "features.json")
    catalog_sha = file_sha256(tmp_path / "catalog.json")
    spec = ModelSpec("tags", "content-tags", "{}", sha, catalog_sha, "test", 42)
    (tmp_path / "spec.json").write_text(json.dumps(asdict(spec)))
    config = {
        "mode": "jamendo",
        "catalog": {"path": "catalog.json", "sha256": catalog_sha},
        "models": [
            {
                "format": "content-tags",
                "path": "features.json",
                "sha256": sha,
                "spec": "spec.json",
                "spec_sha256": file_sha256(tmp_path / "spec.json"),
            }
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_mock_api_to_recommendation_playback_events(tmp_path):
    app = create_app(bundle(tmp_path), tmp_path / "logs.sqlite", model_version="tags")
    app.state.runtime.playback.client = JamendoClient("private", transport=transport([track()]))
    with TestClient(app, headers={"X-Music-App": "1"}) as client:
        assert client.post("/api/sessions", json={"persist": False}).status_code == 200
        assert client.put("/api/profile", json={"items": ["mtg:1"]}).status_code == 200
        result = client.post("/api/recommendations", json={"goal": "discover", "top_k": 5}).json()
        assert result["tracks"][0]["item_id"] == "mtg:2"
        assert result["tracks"][0]["playback_status"] == "unknown"
        playback = client.post(
            "/api/playbacks", json={"request_id": result["request_id"], "item_id": "mtg:2"}
        ).json()
        assert playback["external"] is True
        assert "private" not in json.dumps(playback)
        for kind in ("play", "pause", "ended", "skip"):
            response = client.post(
                "/api/events",
                json={
                    "event_id": kind,
                    "request_id": result["request_id"],
                    "item_id": "mtg:2",
                    "kind": kind,
                    "playback_id": playback["playback_id"],
                    "position_seconds": 1.0,
                },
            )
            assert response.status_code == 200, response.text
        with app.state.runtime.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert (
            app.state.runtime.playback.availability(
                app.state.runtime.context("mtg:2", "test"), datetime.now(UTC)
            ).status
            == "available"
        )


def test_tampered_features_rejected(tmp_path):
    config = bundle(tmp_path)
    (tmp_path / "features.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        create_app(config, tmp_path / "logs.sqlite", model_version="tags")
