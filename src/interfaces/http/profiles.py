"""Owner-only profile routes; no model details or Agent controls."""

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from src.application.study.preferences import PreferenceService


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[str] = Field(default_factory=list, max_length=30)
    artists: list[str] = Field(default_factory=list, max_length=10)
    excluded_items: list[str] = Field(default_factory=list, max_length=1000)


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    choices: list[str] = Field(default_factory=list, max_length=3)
    text: str = Field(default="", max_length=500)


def register_profile_routes(app, runtime, owner, *, researcher):
    service = PreferenceService(runtime.sessions)

    @app.get("/api/profile/long-term")
    def view(request: Request):
        with runtime.lock:
            token, _ = owner(request)
            return service.view(token)

    @app.put("/api/profile/long-term")
    def update(body: Preferences, request: Request):
        with runtime.lock:
            token, _ = owner(request)
            return service.update(token, body.items, body.artists, body.excluded_items)

    if researcher:

        @app.put("/api/session/context")
        def context(body: Context, request: Request):
            with runtime.lock:
                token, _ = owner(request)
                return service.set_context(token, body.choices, body.text)
