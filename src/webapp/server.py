"""FastAPI + React UI。CLIはloopback専用で起動する。"""

import argparse
import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from src.profiles import InitialProfile
from src.webapp.access import AccessVerifier
from src.webapp.runtime import Runtime, now, token_key
from src.webapp.settings import setting
from src.webapp.study import StudyPlan


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Start(Body):
    consent_version: Literal["local-ui-v2", "local-ui-v3", "local-ui-v4"] | None = None
    persist: bool = False
    participant_code: str | None = Field(default=None, min_length=1, max_length=64)
    allow_text: bool = False


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
    kind: Literal["impression", "play", "pause", "ended", "error", "skip", "save", "like", "rating"]
    item_id: str | None = Field(default=None, max_length=200)
    rating: int | None = Field(default=None, ge=1, le=5)
    playback_id: str | None = Field(default=None, max_length=100)
    position_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)

    listened_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    response_latency_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)
    display_latency_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)


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
    config_path, database, *, audience="participant", model_version=None, study_path=None
):
    if audience not in {"participant", "researcher"}:
        raise ValueError("invalid audience")
    study = StudyPlan.read(study_path) if study_path else None
    runtime = Runtime(
        config_path,
        database=database,
        study=study,
        scope={"audience": audience, "model_version": model_version},
    )
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
    if model_version is not None and model_version not in runtime.models:
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
        return {
            "session_id": current.log.session.session_id,
            "registered": current.registered,
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
                for row in current.log.export(now=now())["events"]
                if reply and row["request_id"] == reply["request_id"] and row["kind"] == "like"
            ],
        }

    @asynccontextmanager
    async def lifespan(app):
        async def purge():
            while True:
                await asyncio.sleep(60)
                with runtime.lock:
                    runtime.expire()

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
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

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
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "public, max-age=31536000, immutable"
                if request.url.path.startswith("/assets/")
                else "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.jamendo.com https://*.jamendousercontent.com; media-src 'self' https://*.jamendo.com https://*.jamendousercontent.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
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
        return token, runtime.get(token)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/availability")
    def availability():
        return {
            "state": "online" if study or model_version or researcher else "unconfigured",
            "mode": runtime.mode,
            "checked_at": now().isoformat(),
            "next_open_at": next_open_at,
        }

    @app.get("/api/status")
    def status():
        public = {
            "mode": runtime.mode,
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
            "mode": runtime.mode,
            "study_status": "local-development-only",
            "selection": "comparison-only",
            "models": [
                {
                    "version": spec.version_id,
                    "algorithm": spec.algorithm,
                    "parameters": spec.parameters,
                    "als_fold_in": model.als_fold_in,
                }
                for spec, model in runtime.models.values()
            ],
            "unavailable": [
                name
                for name in ("popularity", "itemcf", "als", "bpr")
                if name not in {spec.algorithm for spec, _ in runtime.models.values()}
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
            for item, row in runtime.tracks.items()
            if query
            in (
                item + " " + (row.get("title") or "") + " " + " ".join(row.get("artists", []))
            ).casefold()
        ]
        return {
            "tracks": [runtime.track(item) for item in matches[offset : offset + 24]],
            "total": len(matches),
            "artists": [artist for artist in runtime.artists if query in artist.casefold()][:24],
        }

    @app.post("/api/participants/check")
    def check_code(body: ParticipantCode):
        with runtime.lock:
            participant = runtime.participants.resolve(body.code, now())
            return {"expires_at": participant["expires_at"]}

    @app.post("/api/participants/code")
    def rotate_code(request: Request):
        with runtime.lock:
            _, current = session(request)
            if not current.registered:
                raise ValueError("参加コードがないセッションです")
            code = runtime.participants.rotate(current.log.session.participant_id, now())
            return {"participant_code": code}

    @app.post("/api/sessions")
    def start(body: Start, request: Request):
        with runtime.lock:
            old = request.cookies.get("music_session")
            participant, code = None, None
            if body.consent_version == "local-ui-v4":
                if not body.persist:
                    raise ValueError("この説明版ではログ保存への同意が必要です")
                if body.participant_code:
                    participant = runtime.participants.resolve(body.participant_code, now())
                elif old:
                    stored = runtime.store.get(token_key(old), now())
                    if stored and stored.get("registered"):
                        participant = runtime.participants.get(
                            stored["session"]["participant_id"], now()
                        )
                if old:
                    try:
                        current = runtime.get(old)
                    except PermissionError:
                        pass
                    else:
                        if (
                            current.registered
                            and not current.finished
                            and participant
                            and participant["participant_id"] == current.log.session.participant_id
                        ):
                            return {**session_state(current), "persist": True}
                if participant is None:
                    if retention_days is None:
                        raise ValueError("サーバー管理者が保存期間を設定してから開始してください")
                    participant, code = runtime.participants.issue(
                        expires_at=now() + timedelta(days=retention_days), now=now()
                    )
            elif old:
                runtime.delete(old)
            resumed = (
                runtime.resume_participant(participant["participant_id"]) if participant else None
            )
            if resumed:
                token, current = resumed
            else:
                token, current = runtime.start(
                    body.persist,
                    body.allow_text,
                    consent_version=body.consent_version,
                    participant=participant,
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
            if current.finished:
                raise ValueError("終了済みです")
            if study and current.recommendations:
                raise ValueError("比較中の初期プロフィールは固定されています")
            if (
                set(body.items) - runtime.tracks.keys()
                or set(body.artists) - runtime.artists.keys()
            ):
                raise ValueError("選択した曲・アーティストがありません")
            current.profile = InitialProfile(
                current.profile.user_id, tuple(body.items), tuple(body.artists)
            )
            runtime.save(token, current)
            if current.registered:
                runtime.participants.save_profile(
                    current.profile.user_id,
                    {"selected_item_ids": body.items, "selected_artist_ids": body.artists},
                    runtime.resume_context["config_sha256"],
                    now(),
                )
            return {"selected_count": len(body.items), "artist_count": len(body.artists)}

    @app.post("/api/recommendations")
    def recommend(body: Recommend, request: Request):
        with runtime.lock:
            token, current = session(request)
            if not researcher and body.version is not None:
                raise ValueError("推薦条件はサーバーで割り当てます")
            if current.finished:
                raise ValueError("終了済みです")
            if study:
                if current.phase != "listen":
                    raise ValueError("試聴画面で実行してください")
                if current.period in current.replies:
                    return public_reply(current.replies[current.period])
                condition = study.conditions[current.order[current.period]]
                reply = runtime.recommend(token, condition.version, "discover", study.top_k, "")
            else:
                version = body.version if researcher else model_version
                reply = runtime.recommend(token, version, body.goal, body.top_k, body.text)
            return public_reply(reply)

    @app.post("/api/events")
    def event(body: Event, request: Request):
        with runtime.lock:
            token, _ = session(request)
            runtime.event(token, body.model_dump())
            return {"accepted": True}

    @app.post("/api/previews")
    def preview(body: Preview, request: Request):
        with runtime.lock:
            token, current = session(request)
            if current.finished or current.phase != "profile" or current.recommendations:
                raise ValueError("好みを選ぶ画面でのみ試聴できます")
            if body.item_id not in runtime.tracks:
                raise ValueError("楽曲がありません")
            if len(current.preview_bindings) >= 500:
                raise ValueError("試聴回数上限です")
            context = runtime.context(body.item_id, current.profile.user_id)
            external = (
                runtime.playback.resolve(context, now()) if runtime.mode == "jamendo" else None
            )
            if external is None:
                runtime.playback.file(context, now())
            preview_id = str(uuid4())
            current.preview_bindings[preview_id] = body.item_id
            runtime.save(token, current)
            return {
                "url": external["audio"] if external else f"/api/preview-audio/{preview_id}",
                "external": external is not None,
                "license_url": external["license_ccurl"] if external else None,
            }

    @app.get("/api/preview-audio/{preview_id}")
    def preview_audio(preview_id: str, request: Request):
        with runtime.lock:
            _, current = session(request)
            item = current.preview_bindings.get(preview_id)
            if (
                current.finished
                or current.phase != "profile"
                or current.recommendations
                or item is None
            ):
                raise HTTPException(404, "試聴が見つかりません")
            return FileResponse(
                runtime.playback.file(runtime.context(item, current.profile.user_id), now())
            )

    @app.post("/api/playbacks")
    def playback(body: Playback, request: Request):
        with runtime.lock:
            token, current = session(request)
            runtime.require_current_request(current, body.request_id)
            if current.finished or body.item_id not in current.recommendations.get(
                body.request_id, ()
            ):
                raise ValueError("このセッションの推薦曲ではありません")
            external = None
            context = runtime.context(body.item_id, current.profile.user_id)
            if runtime.mode == "jamendo":
                external = runtime.playback.resolve(context, now())
            else:
                runtime.playback.file(context, now())
            if len(current.playback_bindings) >= 500:
                raise ValueError("試聴回数上限です")
            playback_id = str(uuid4())
            current.playback_bindings[playback_id] = (body.request_id, body.item_id)
            runtime.save(token, current)
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
            binding = current.playback_bindings.get(playback_id)
            if current.finished or binding is None:
                raise HTTPException(404, "試聴が見つかりません")
            runtime.require_current_request(current, binding[0])
            path = runtime.playback.file(
                runtime.context(binding[1], current.profile.user_id), now()
            )
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
                "mode": runtime.mode,
                "funnel": current.funnel,
            }

    @app.post("/api/session/finish")
    def finish(request: Request):
        with runtime.lock:
            token, current = session(request)
            if study and current.period != len(current.order) - 1:
                raise ValueError("途中終了は離脱操作を使用してください")
            if study and current.phase != "evaluate":
                raise ValueError("評価画面で終了してください")
            runtime.progress(token, current.phase, "exit")
            current.finished = True
            current.termination_reason = "completed"
            runtime.save(token, current)
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
            runtime.progress(token, body.phase, body.kind)
            return {"accepted": True}

    @app.post("/api/session/next")
    def next_period(body: Advance, request: Request):
        with runtime.lock:
            token, current = session(request)
            if not study or current.finished or body.period != current.period + 1:
                raise ValueError("条件の遷移が不正です")
            if current.phase != "evaluate" or current.period not in current.replies:
                raise ValueError("現在の試聴を終えてから進んでください")
            if current.period + 1 >= len(current.order):
                raise ValueError("最後の条件です")
            current.period += 1
            runtime.progress(token, "listen")
            return session_state(current)

    @app.post("/api/session/leave")
    def leave(request: Request):
        with runtime.lock:
            token, current = session(request)
            runtime.progress(token, current.phase, "exit")
            current.finished = True
            current.termination_reason = "abandoned"
            runtime.save(token, current)
            return {"finished": True}

    @app.post("/api/session/logout")
    def logout(request: Request):
        with runtime.lock:
            token, current = session(request)
            if not current.finished:
                runtime.progress(token, current.phase, "exit")
                current.finished = True
                current.termination_reason = "abandoned"
                runtime.save(token, current)
            response = JSONResponse({"signed_out": True})
            response.delete_cookie("music_session")
            return response

    @app.delete("/api/session")
    def delete(request: Request):
        with runtime.lock:
            runtime.delete(request.cookies.get("music_session", ""))
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
    parser.add_argument("--jamendo-env-file", type=Path)
    args = parser.parse_args()
    from src.jamendo import configure_credentials

    configure_credentials(args.jamendo_env_file)
    if args.host not in {"127.0.0.1", "localhost"} and not setting("API_TOKEN"):
        parser.error("external binding requires MUSIC_RECSYS_API_TOKEN and explicit origins")
    uvicorn.run(
        create_app(
            args.config,
            args.database,
            audience=args.audience,
            model_version=args.model_version,
            study_path=args.study,
        ),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
