"""Explicit consent for local synthetic participant API tests."""


def start_session(client, **kwargs):
    body = {"persist": True, "consent_version": "local-ui-v8", **kwargs}
    return client.post("/api/sessions", json=body)
