"""研究者向けのセッション内要望記録。"""

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    choices: list[str] = Field(default_factory=list, max_length=3)
    text: str = Field(default="", max_length=500)


def register_context_routes(app, runtime, owner):
    @app.put("/api/session/context")
    def context(body: Context, request: Request):
        with runtime.lock:
            token, _ = owner(request)
            return runtime.sessions.set_context(token, body.choices, body.text)
