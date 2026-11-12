"""Playback and event transport; authorization and event rules stay in application services."""

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from backend.interfaces.http.schemas import Event, Playback, Preview


def register_playback_routes(app, views):
    runtime, session = views.runtime, views.owner

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
