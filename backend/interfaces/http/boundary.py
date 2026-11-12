"""Host/authentication/origin boundary and HTTP error mapping for each app instance."""

import secrets
from collections import deque
from time import monotonic

from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.infrastructure.auth.access import AccessVerifier
from backend.infrastructure.config.settings import setting


def install_boundary(app, *, researcher):
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

    failed_entries = deque()

    @app.middleware("http")
    async def local_boundary(request, call_next):
        host = request.url.hostname
        if host not in allowed_hosts:
            return JSONResponse({"detail": "localhost専用です"}, 403)
        request.state.access_identity = None
        if access:
            request.state.access_identity = await run_in_threadpool(
                access.identity, request.headers.get("cf-access-jwt-assertion", "")
            )
            if request.state.access_identity is None:
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
        entry = request.method == "POST" and request.url.path == "/api/sessions"
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

    return access, bool(api_token or access)
