"""Session, profile and assigned recommendation HTTP routes."""

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.interfaces.http.schemas import Advance, Profile, Progress, Recommend, Start
from backend.interfaces.http.session_context import register_context_routes


def register_session_routes(app, views):
    runtime, session = views.runtime, views.owner
    researcher, access = views.researcher, views.access
    model_version, retention_days = views.model_version, views.retention_days
    session_state, public_reply, now = views.state, views.public_reply, views.clock
    if researcher:
        register_context_routes(app, runtime, session)

    @app.post("/api/sessions")
    def start(body: Start, request: Request):
        with runtime.lock:
            if not researcher:
                if (
                    body.consent_version
                    not in {
                        "local-ui-v4",
                        "local-ui-v5",
                        "local-ui-v6",
                        "local-ui-v7",
                        "local-ui-v8",
                    }
                    or not body.persist
                ):
                    raise ValueError("利用説明を確認し、保存に同意してください")
            token, current = runtime.sessions.open_session(
                old_token=request.cookies.get("music_session"),
                persist=body.persist,
                allow_text=body.allow_text,
                consent_version=body.consent_version,
                retention_days=retention_days,
                identity_key=request.state.access_identity,
            )
            response = JSONResponse(
                {
                    **session_state(current),
                    "persist": current.persist,
                }
            )
            response.set_cookie(
                "music_session",
                token,
                httponly=True,
                samesite="strict",
                secure=views.secure_cookie,
                max_age=max(1, int((current.log.session.retain_until - now()).total_seconds())),
            )
            return response

    @app.put("/api/profile")
    def profile(body: Profile, request: Request):
        with runtime.lock:
            token, current = session(request)
            return runtime.sessions.set_profile(token, body.items, body.artists)

    @app.post("/api/recommendations")
    def recommend(body: Recommend, request: Request):
        with runtime.lock:
            token, current = session(request)
            if not researcher and body.version is not None:
                raise ValueError("推薦条件はサーバーで割り当てます")
            reply = runtime.recommendations.assigned_recommendation(
                token,
                body.version if researcher else model_version,
                body.goal,
                body.top_k,
                body.text,
            )
            return public_reply(reply)

    @app.get("/api/session/export")
    def export(request: Request):
        with runtime.lock:
            _, current = session(request)
            payload = current.log.export(now=now())
            if not researcher:
                payload["requests"] = [
                    {key: row[key] for key in ("request_id", "recorded_at", "response")}
                    for row in payload["requests"]
                ]
                # 応答内にもモデル名・スコア等を出さない。
                for row in payload["requests"]:
                    row["response"] = {"item_ids": row["response"]["item_ids"]}
                return {
                    "events": payload["events"],
                    "requests": payload["requests"],
                    "funnel": current.funnel,
                }
            return {
                **payload,
                "goals": current.goals,
                "mode": runtime.catalog.mode,
                "funnel": current.funnel,
            }

    @app.post("/api/session/finish")
    def finish(request: Request):
        with runtime.lock:
            token, current = session(request)
            runtime.sessions.finish(token)
            return {"finished": True}

    @app.get("/api/session/state")
    def state(request: Request):
        with runtime.lock:
            _, current = session(request)
            return session_state(current)

    @app.post("/api/session/progress")
    def progress(body: Progress, request: Request):
        with runtime.lock:
            token, _ = session(request)
            runtime.sessions.progress(token, body.phase, body.kind)
            return {"accepted": True}

    @app.post("/api/session/next")
    def next_period(body: Advance, request: Request):
        with runtime.lock:
            token, current = session(request)
            current = runtime.sessions.next_period(token, body.period)
            return session_state(current)

    @app.post("/api/session/leave")
    def leave(request: Request):
        with runtime.lock:
            token, current = session(request)
            runtime.sessions.leave(token)
            return {"finished": True}

    @app.post("/api/session/logout")
    def logout(request: Request):
        with runtime.lock:
            token, current = session(request)
            if not current.finished:
                runtime.sessions.leave(token)
            response = JSONResponse({"signed_out": True})
            response.delete_cookie("music_session")
            return response

    @app.delete("/api/session")
    def delete(request: Request):
        with runtime.lock:
            if access and request.cookies.get("music_session"):
                session(request)
            runtime.sessions.delete(request.cookies.get("music_session", ""))
            if runtime.control is not None:
                runtime.control.expire()
            response = JSONResponse({"deleted": True})
            response.delete_cookie("music_session")
            return response
