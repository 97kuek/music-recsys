"""Local researcher-only descriptive views; no persistent cross-participant export cache."""

import json
from typing import Literal

from fastapi import Response

from src.application.study.analysis import analyze_repository
from src.interfaces.online_export import analysis_csv


def register_analysis_routes(app, runtime, require_researcher):
    def read():
        require_researcher()
        with runtime.lock:
            runtime.sessions.expire()
            return analyze_repository(
                runtime.sessions.store,
                now=runtime.sessions.clock(),
                plans=[runtime.sessions.study] if runtime.sessions.study else [],
            )

    @app.get("/api/online-analysis")
    def analysis():
        return read()

    @app.get("/api/online-analysis/export")
    def export(format: Literal["json", "csv"] = "json", table: str = "lists"):
        result = read()
        encoded = (
            analysis_csv(result, table)
            if format == "csv"
            else json.dumps(result, ensure_ascii=False, allow_nan=False)
        )
        return Response(
            encoded,
            media_type="text/csv" if format == "csv" else "application/json",
            headers={"Content-Disposition": f'attachment; filename="online-analysis.{format}"'},
        )
