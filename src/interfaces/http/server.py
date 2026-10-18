"""FastAPI + React UI。CLIはloopback専用で起動する。"""

import argparse
import asyncio
import os
import secrets
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.application.study.clock import utc_now as now
from src.bootstrap import create_runtime
from src.infrastructure.auth.access import AccessVerifier
from src.infrastructure.config.settings import setting
from src.infrastructure.config.study import read_study_plan


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Start(Body):
    consent_version: (
        Literal["local-ui-v2", "local-ui-v3", "local-ui-v4", "local-ui-v5", "local-ui-v6"] | None
    ) = None
    persist: bool = False
    participant_code: str | None = Field(default=None, min_length=1, max_length=64)
    allow_text: bool = False
    long_term_consent: bool = False


class ParticipantCode(Body):
    code: str = Field(min_length=1, max_length=64)


class Profile(Body):
    items: list[str] = Field(default_factory=list, max_length=30)
    artists: list[str] = Field(default_factory=list, max_length=10)


class Recommend(Body):
    version: str | None = Field(default=None, max_length=200)
    goal: Literal["discover", "familiar", "auto"] = "auto"
    top_k: int = Field(default=10, ge=2, le=20)
    text: str = Field(default="", max_length=500)


class Event(Body):
    event_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    kind: Literal[
        "impression",
        "play",
        "pause",
        "playback_progress",
        "ended",
        "error",
        "skip",
        "save",
        "like",
        "rating",
        "track_switch",
        "evaluation_shown",
        "list_evaluation",
        "observation_gap",
    ]
    item_id: str | None = Field(default=None, max_length=200)
    rating: int | None = Field(default=None, ge=1, le=5)
    playback_id: str | None = Field(default=None, max_length=100)
    position_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)

    listened_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    response_latency_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)
    display_latency_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)
    event_version: Literal[1, 2] = 1
    movement: Literal["next", "previous", "direct"] | None = None
    target_item_id: str | None = Field(default=None, max_length=200)
    was_playing: bool | None = None
    discovery_status: Literal["unanswered", "none", "selected"] | None = None
    discovery_item_ids: list[str] = Field(default_factory=list, max_length=20)
    observation_complete: bool | None = None


class Preview(Body):
    item_id: str = Field(min_length=1, max_length=200)


class Playback(Body):
    request_id: str = Field(max_length=100)
    item_id: str = Field(max_length=200)


class Progress(Body):
    phase: Literal["profile", "listen", "evaluate"]
    kind: Literal["enter", "hidden", "visible", "exit"] = "enter"


class Advance(Body):
    period: int = Field(ge=1)


def create_app(
    config_path,
    database,
    *,
    audience="participant",
    model_version=None,
    study_path=None,
    control_path=None,
    control_factory=None,
):
    if audience not in {"participant", "researcher"}:
        raise ValueError("invalid audience")
    if audience != "researcher" and (control_path is not None or control_factory is not None):
        raise ValueError("strategy control is available only to the local researcher")
    if control_path is not None and control_factory is not None:
        raise ValueError("supply one explicit control configuration")
    study = read_study_plan(study_path) if study_path else None
    runtime = create_runtime(
        config_path,
        database=database,
        study=study,
        scope={"audience": audience, "model_version": model_version},
    )
    if control_path is not None:
        from src.infrastructure.config.control import build_control

        runtime.control = build_control(runtime, control_path)
    elif control_factory is not None:
        runtime.control = control_factory(runtime)
    retention_days = setting("RETENTION_DAYS")
    retention_days = int(retention_days) if retention_days else None
    if retention_days is not None and not 1 <= retention_days <= 366:
        raise ValueError("RETENTION_DAYS must be between 1 and 366")
    next_open_at = setting("NEXT_OPEN_AT") or None
    if next_open_at:
        opening = datetime.fromisoformat(next_open_at)
        if opening.utcoffset() is None:
            raise ValueError("NEXT_OPEN_AT requires a timezone")
        next_open_at = opening.astimezone(UTC).isoformat()
    if model_version is not None and model_version not in runtime.recommendations.models:
        raise ValueError("normal-mode model is not ready")
    researcher = audience == "researcher"
    api_token = setting("API_TOKEN")
    access_team = setting("ACCESS_TEAM")
    access_aud = setting("ACCESS_AUD")
    access = AccessVerifier(access_team, access_aud) if access_team or access_aud else None
    allowed_origins = set(filter(None, setting("ALLOWED_ORIGINS").split(",")))
    allowed_hosts = {"localhost", "127.0.0.1", "testserver"} | set(
        filter(None, setting("ALLOWED_HOSTS").split(","))
    )
    if api_token and (len(api_token) < 32 or not allowed_origins or researcher):
        raise ValueError(
            "external API requires a strong token, explicit origins and participant audience"
        )
    if access and (
        researcher
        or not allowed_origins
        or any(not o.startswith("https://") for o in allowed_origins)
    ):
        raise ValueError("Access requires HTTPS origins and participant audience")
    if allowed_origins and not (api_token or access):
        raise ValueError("external origins require server-side authentication")

    def require_researcher():
        if not researcher:
            raise HTTPException(404, "not found")

    def public_reply(reply):
        if researcher:
            return reply
        return {key: reply[key] for key in ("request_id", "tracks", "notice")}

    def session_state(current):
        reply = current.replies.get(current.period)
        events = current.log.export(now=now())["events"]
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
            "recommendation": public_reply(reply) if reply else None,
            "actions": [
                f"like:{row['item_id']}"
                for row in events
                if reply and row["request_id"] == reply["request_id"] and row["kind"] == "like"
            ],
            "list_evaluation": list_evaluation,
            "long_term_consent": current.long_term_consent,
            "profile_requires_selection": current.profile_snapshot.get("requires_selection", False),
            "profile_locked": current.assignment.get("input") is not None,
        }

    @asynccontextmanager
    async def lifespan(app):
        async def purge():
            while True:
                await asyncio.sleep(60)
                with runtime.lock:
                    runtime.sessions.expire()
                    if runtime.control is not None:
                        runtime.control.expire()

        task = asyncio.create_task(purge())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="music-recsys API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    from src.interfaces.http.online_analysis import register_analysis_routes

    register_analysis_routes(app, runtime, require_researcher)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

    failed_entries = deque()

    @app.middleware("http")
    async def local_boundary(request, call_next):
        host = request.url.hostname
        if host not in allowed_hosts:
            return JSONResponse({"detail": "localhost専用です"}, 403)
        if access and not await run_in_threadpool(
            access.verify, request.headers.get("cf-access-jwt-assertion", "")
        ):
            return JSONResponse(
                {"detail": "unauthorized"}, 401, headers={"Cache-Control": "no-store"}
            )
        if request.url.path.startswith("/api/") and api_token:
            if not secrets.compare_digest(
                request.headers.get("authorization", ""), f"Bearer {api_token}"
            ):
                return JSONResponse(
                    {"detail": "unauthorized"}, 401, headers={"Cache-Control": "no-store"}
                )
        if request.method in {"POST", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-music-app") != "1" or (
                origin and origin not in (allowed_origins or {str(request.base_url).rstrip("/")})
            ):
                return JSONResponse({"detail": "同一画面から操作してください"}, 403)
            if len(await request.body()) > 16384:
                return JSONResponse({"detail": "入力が大きすぎます"}, 413)
        entry = request.method == "POST" and request.url.path in {
            "/api/sessions",
            "/api/participants/check",
        }
        if entry and not researcher:
            while failed_entries and failed_entries[0] <= monotonic() - 60:
                failed_entries.popleft()
            if len(failed_entries) >= 10:
                return JSONResponse(
                    {"detail": "入力の試行が多いため、1分待ってから再試行してください"},
                    429,
                    headers={"Retry-After": "60", "Cache-Control": "no-store"},
                )
        response = await call_next(request)
        if entry and not researcher and response.status_code in {400, 401, 422}:
            failed_entries.append(monotonic())
        response.headers.update(
            {
                "Cache-Control": "public, max-age=31536000, immutable"
                if request.url.path.startswith("/assets/")
                else "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exception):
        return JSONResponse({"detail": str(exception)}, 400)

    @app.exception_handler(PermissionError)
    async def denied(request, exception):
        return JSONResponse({"detail": str(exception)}, 401)

    def session(request):
        token = request.cookies.get("music_session", "")
        return token, runtime.sessions.get(token)

    from src.interfaces.http.control import register_control_routes

    register_control_routes(app, runtime, require_researcher, session)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/availability")
    def availability():
        return {
            "state": "online" if study or model_version or researcher else "unconfigured",
            "mode": runtime.catalog.mode,
            "checked_at": now().isoformat(),
            "next_open_at": next_open_at,
        }

    @app.get("/api/status")
    def status():
        public = {
            "mode": runtime.catalog.mode,
            "audience": audience,
            "experience": "experiment" if study else "normal",
            "ready": bool(study or model_version or researcher),
            "catalog_key": runtime.config["catalog"]["sha256"],
            "study_status": "local-development-only",
            "retention_days": retention_days,
        }
        if not researcher:
            return public
        return {
            **public,
            "mode": runtime.catalog.mode,
            "study_status": "local-development-only",
            "selection": "comparison-only",
            "models": [
                {
                    "version": spec.version_id,
                    "algorithm": spec.algorithm,
                    "parameters": spec.parameters,
                    "als_fold_in": model.als_fold_in,
                }
                for spec, model in runtime.recommendations.models.values()
            ],
            "unavailable": [
                name
                for name in ("popularity", "itemcf", "als", "bpr")
                if name
                not in {spec.algorithm for spec, _ in runtime.recommendations.models.values()}
            ],
        }

    @app.get("/api/comparison")
    def comparison():
        require_researcher()
        return runtime.config.get("comparison", [])

    @app.get("/api/catalog")
    def catalog(q: str = "", offset: int = 0):
        if len(q) > 200 or offset < 0:
            raise ValueError("検索条件が不正です")
        query = q.casefold()
        matches = [
            item
            for item, row in runtime.catalog.tracks.items()
            if query
            in (
                item + " " + (row.get("title") or "") + " " + " ".join(row.get("artists", []))
            ).casefold()
        ]
        return {
            "tracks": [runtime.catalog.track(item) for item in matches[offset : offset + 24]],
            "total": len(matches),
            "artists": [artist for artist in runtime.catalog.artists if query in artist.casefold()][
                :24
            ],
        }

    @app.post("/api/participants/check")
    def check_code(body: ParticipantCode):
        with runtime.lock:
            participant = runtime.sessions.participants.resolve(body.code, now())
            return {"expires_at": participant["expires_at"]}

    @app.post("/api/participants/code")
    def rotate_code(request: Request):
        with runtime.lock:
            _, current = session(request)
            if not current.registered:
                raise ValueError("参加コードがないセッションです")
            code = runtime.sessions.participants.rotate(current.log.session.participant_id, now())
            return {"participant_code": code}

    @app.post("/api/sessions")
    def start(body: Start, request: Request):
        with runtime.lock:
            if not researcher:
                if (
                    body.consent_version not in {"local-ui-v4", "local-ui-v5", "local-ui-v6"}
                    or not body.persist
                ):
                    raise ValueError("利用説明を確認し、保存に同意して参加コードを入力してください")
                if not body.participant_code:
                    _, previous = session(request)
                    if not previous.registered:
                        raise PermissionError("実施者から配布された参加コードを入力してください")
            token, current, code = runtime.sessions.open_session(
                old_token=request.cookies.get("music_session"),
                persist=body.persist,
                allow_text=body.allow_text,
                consent_version=body.consent_version,
                participant_code=body.participant_code,
                retention_days=retention_days,
                long_term_consent=body.long_term_consent,
            )
            response = JSONResponse(
                {
                    **session_state(current),
                    "persist": current.persist,
                    **({"participant_code": code} if code else {}),
                }
            )
            response.set_cookie(
                "music_session",
                token,
                httponly=True,
                samesite="strict",
                secure=bool(api_token or access),
                max_age=max(1, int((current.log.session.retain_until - now()).total_seconds())),
            )
            return response

    @app.put("/api/profile")
    def profile(body: Profile, request: Request):
        with runtime.lock:
            token, current = session(request)
            return runtime.sessions.set_profile(token, body.items, body.artists)

    from src.interfaces.http.profiles import register_profile_routes

    register_profile_routes(app, runtime, session, researcher=researcher)

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

    @app.post("/api/events")
    def event(body: Event, request: Request):
        with runtime.lock:
            token, _ = session(request)
            runtime.playback.event(token, body.model_dump())
            return {"accepted": True}

    @app.post("/api/previews")
    def preview(body: Preview, request: Request):
        with runtime.lock:
            token, current = session(request)

            preview_id, external = runtime.playback.bind_preview(token, body.item_id)
            return {
                "url": external["audio"] if external else f"/api/preview-audio/{preview_id}",
                "external": external is not None,
                "license_url": external["license_ccurl"] if external else None,
            }

    @app.get("/api/preview-audio/{preview_id}")
    def preview_audio(preview_id: str, request: Request):
        with runtime.lock:
            _, current = session(request)
            try:
                path = runtime.playback.preview_file(
                    request.cookies.get("music_session", ""), preview_id
                )
            except LookupError as error:
                raise HTTPException(404, str(error)) from error
            return FileResponse(path)

    @app.post("/api/playbacks")
    def playback(body: Playback, request: Request):
        with runtime.lock:
            token, current = session(request)

            playback_id, external = runtime.playback.bind_playback(
                token, body.item_id, body.request_id
            )
            return {
                "playback_id": playback_id,
                "url": external["audio"] if external else f"/api/audio/{playback_id}",
                "external": external is not None,
                "license_url": external["license_ccurl"] if external else None,
            }

    @app.get("/api/audio/{playback_id}")
    def audio(playback_id: str, request: Request):
        with runtime.lock:
            _, current = session(request)
            try:
                path = runtime.playback.playback_file(
                    request.cookies.get("music_session", ""), playback_id
                )
            except LookupError as error:
                raise HTTPException(404, str(error)) from error
            return FileResponse(path)

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
            runtime.sessions.delete(request.cookies.get("music_session", ""))
            if runtime.control is not None:
                runtime.control.expire()
            response = JSONResponse({"deleted": True})
            response.delete_cookie("music_session")
            return response

    static = Path(__file__).parent / "static"
    if (static / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    else:

        @app.get("/")
        def missing_ui():
            return JSONResponse(
                {"detail": "frontendで npm ci && npm run build を実行してください"}, 503
            )

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path("data/app/local.sqlite"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--audience", choices=["participant", "researcher"], default="participant")
    parser.add_argument("--model-version", help="通常モード用の明示設定。採用設定の確定ではない")
    parser.add_argument("--study", type=Path, help="サーバー側の比較計画JSON")
    parser.add_argument("--control", type=Path, help="研究者専用の明示LangGraph設定JSON")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"} and not setting("API_TOKEN"):
        parser.error("external binding requires MUSIC_RECSYS_API_TOKEN and explicit origins")
    uvicorn.run(
        create_app(
            args.config,
            args.database,
            audience=args.audience,
            model_version=args.model_version,
            study_path=args.study,
            control_path=args.control,
        ),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
