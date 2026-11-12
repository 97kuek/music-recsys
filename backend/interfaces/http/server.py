"""Compose the FastAPI runtime, HTTP boundary and route adapters; CLI defaults to loopback."""

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.application.study.clock import utc_now as now
from backend.bootstrap import create_runtime
from backend.infrastructure.config.settings import setting
from backend.infrastructure.config.study import read_study_plan
from backend.interfaces.http.boundary import install_boundary
from backend.interfaces.http.catalog import register_catalog_routes
from backend.interfaces.http.control import register_control_routes
from backend.interfaces.http.online_analysis import register_analysis_routes
from backend.interfaces.http.playback import register_playback_routes
from backend.interfaces.http.session_views import SessionViews
from backend.interfaces.http.sessions import register_session_routes


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
        from backend.infrastructure.config.control import build_control

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
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    access, secure_cookie = install_boundary(app, researcher=researcher)
    views = SessionViews(
        runtime=runtime,
        audience=audience,
        model_version=model_version,
        retention_days=retention_days,
        access=bool(access),
        secure_cookie=secure_cookie,
        clock=lambda: now(),
    )
    register_analysis_routes(app, runtime, views.require_researcher)
    register_control_routes(app, runtime, views.require_researcher, views.owner)
    register_catalog_routes(app, views, study=study, next_open_at=next_open_at)
    register_session_routes(app, views)
    register_playback_routes(app, views)

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
