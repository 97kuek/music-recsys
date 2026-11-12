"""Session ownership and participant/researcher response projections for HTTP adapters."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException

from backend.application.study.runtime import Runtime


@dataclass(frozen=True)
class SessionViews:
    runtime: Runtime
    audience: str
    model_version: str | None
    retention_days: int | None
    access: bool
    secure_cookie: bool
    clock: Callable[[], datetime]

    @property
    def researcher(self):
        return self.audience == "researcher"

    def require_researcher(self):
        if not self.researcher:
            raise HTTPException(404, "not found")

    def public_reply(self, reply):
        if self.researcher:
            return reply
        return {key: reply[key] for key in ("request_id", "tracks", "notice")}

    def state(self, current):
        reply = current.replies.get(current.period)
        events = current.log.export(now=self.clock())["events"]
        answers = [e for e in events if reply and e["request_id"] == reply["request_id"]]
        list_evaluation = next((e for e in answers if e["kind"] == "list_evaluation"), None)
        legacy = [e for e in answers if e["kind"] == "rating" and e.get("item_id") is None]
        if list_evaluation is None and legacy:
            list_evaluation = {
                "rating": legacy[0]["rating"] if len(legacy) == 1 else None,
                "discovery_status": "unanswered",
                "discovery_item_ids": [],
                "observation_complete": False,
            }
        return {
            "session_id": current.log.session.session_id,
            "registered": current.registered,
            "consent_version": current.log.session.consent_version,
            "expires_at": current.log.session.retain_until.isoformat(),
            "phase": current.phase,
            "period": current.period + 1,
            "periods": len(current.order) or 1,
            "finished": current.finished,
            "profile": {
                "items": current.profile.selected_item_ids,
                "artists": current.profile.selected_artist_ids,
            },
            "recommendation": self.public_reply(reply) if reply else None,
            "actions": [
                f"like:{row['item_id']}"
                for row in events
                if reply and row["request_id"] == reply["request_id"] and row["kind"] == "like"
            ],
            "list_evaluation": list_evaluation,
            "profile_locked": current.assignment.get("input") is not None,
        }

    def owner(self, request):
        token = request.cookies.get("music_session", "")
        current = self.runtime.sessions.get(token)
        if self.access:
            if not current.registered:
                raise PermissionError("利用説明への同意が必要です")
            self.runtime.sessions.participants.bind(
                current.profile.user_id, request.state.access_identity, self.clock()
            )
        return token, current
