"""Jamendo read API。認証値・上流例外URLを公開せず、音源を保存しない。"""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from music_recsys.catalog import PlaybackAvailability, normalize


class JamendoError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Jamendo: {code}")


def configure_credentials(path: Path | None):
    """指定ファイルの当該キーだけ読む。shell展開・他の変数の読込みはしない。"""
    if path is None or os.environ.get("JAMENDO_CLIENT_ID"):
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key == "JAMENDO_CLIENT_ID":
            value = value.strip().strip("\"'")
            if not value.isalnum():
                raise ValueError("JAMENDO_CLIENT_ID must be alphanumeric")
            os.environ[key] = value
            return


def public_url(value, secret=""):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not any(
            host == root or host.endswith("." + root)
            for root in ("jamendo.com", "jamendousercontent.com")
        )
        or "client_id" in parse_qs(parsed.query)
        or (secret and secret in value)
    ):
        return None
    return value


class JamendoClient:
    def __init__(self, client_id=None, *, transport=None):
        self._key = client_id if client_id is not None else os.environ.get("JAMENDO_CLIENT_ID", "")
        self.transport = transport

    @property
    def configured(self):
        return bool(self._key)

    def tracks(self, ids):
        if not self._key:
            raise JamendoError("not_configured")
        if not ids or len(ids) > 200 or any(not str(i).isdigit() for i in ids):
            raise ValueError("numeric track IDs required (1..200)")
        try:
            with httpx.Client(
                timeout=15, transport=self.transport, follow_redirects=False
            ) as client:
                response = client.get(
                    "https://api.jamendo.com/v3.0/tracks/",
                    params={
                        "client_id": self._key,
                        "format": "json",
                        "id": "+".join(ids),
                        "limit": 200,
                        "include": "licenses",
                        "type": "single+albumtrack",
                        "audioformat": "mp31",
                    },
                )
            if response.status_code == 429:
                raise JamendoError("rate_limited")
            if response.status_code in (401, 403):
                raise JamendoError("authentication_denied")
            if response.status_code != 200:
                raise JamendoError(f"http_{response.status_code}")
            data = response.json()
            if data["headers"]["status"] != "success":
                code = data["headers"].get("code")
                raise JamendoError("application_suspended" if code == 11 else "api_error")
            if not isinstance(data["results"], list):
                raise JamendoError("invalid_response")
            result = {}
            for row in data["results"]:
                track_id = str(row["id"])
                if track_id not in ids or track_id in result:
                    raise JamendoError("unexpected_track_id")
                result[track_id] = {
                    k: row.get(k)
                    for k in ("id", "name", "artist_name", "artist_id", "duration", "license_ccurl")
                }
                for key in ("audio", "image", "shareurl"):
                    result[track_id][key] = public_url(row.get(key), self._key)
            return result
        except JamendoError:
            raise
        except httpx.HTTPError:
            raise JamendoError("network_error") from None
        except (ValueError, KeyError, TypeError):
            raise JamendoError("invalid_response") from None


def match_record(expected, current):
    if current is None:
        return "not_returned"  # 削除と地域制限は、この応答だけでは区別不能
    if str(current["id"]) != str(expected["source_id"]):
        return "id_mismatch"
    if normalize(current.get("name") or "") != normalize(expected["title"]) or normalize(
        current.get("artist_name") or ""
    ) != normalize(expected["artist"]):
        return "metadata_conflict"
    return "id_metadata_consistent"  # 音声指紋による同一録音の証明ではない


class JamendoPlayback:
    """再生開始時にURLを解決。availableはURL提供可、実再生成功とは別。"""

    def __init__(self, records, client=None):
        self.records = {r["source_id"]: r for r in records}
        self.client = client or JamendoClient()
        self.cache = {}

    def availability(self, context, now):
        cached = self.cache.get(context.ref.source_id)
        status = "unknown"
        if cached and now < cached[0]:
            status = "available"
        return PlaybackAvailability(context, status, now, now + timedelta(seconds=30))

    def resolve(self, context, now):
        item = context.ref.source_id
        cached = self.cache.get(item)
        if cached and now < cached[0]:
            return cached[1]
        expected = self.records[item]
        row = self.client.tracks([expected["source_id"]]).get(expected["source_id"])
        match = match_record(expected, row)
        if match != "id_metadata_consistent":
            raise JamendoError(match)
        license_url = row.get("license_ccurl") or ""
        if not license_url.startswith(
            ("https://creativecommons.org/licenses/", "http://creativecommons.org/licenses/")
        ):
            raise JamendoError("license_unverified")
        if not row["audio"]:
            raise JamendoError("stream_url_unavailable")
        self.cache[item] = (now + timedelta(seconds=60), row)
        return row

    def file(self, context, now):
        raise JamendoError("external_audio_not_hosted")
