"""Synthetic invitations issued locally before a participant enters the HTTP API."""

from datetime import timedelta


def issue_code(app, days=90):
    sessions = app.state.runtime.sessions
    now = sessions.clock()
    _, code = sessions.participants.issue(expires_at=now + timedelta(days=days), now=now)
    return code


def invitation(client, days=90):
    if client.cookies.get("music_session"):
        return {}
    client.issued_code = issue_code(client.app, days)
    return {"participant_code": client.issued_code}


def invited_start(client, **kwargs):
    body = {"persist": True, "consent_version": "local-ui-v4", **invitation(client), **kwargs}
    return client.post("/api/sessions", json=body)
