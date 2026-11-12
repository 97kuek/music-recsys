"""Read-only health, availability, model visibility and catalog HTTP routes."""


def register_catalog_routes(app, views, *, study, next_open_at):
    runtime, researcher, audience = views.runtime, views.researcher, views.audience
    model_version, retention_days = views.model_version, views.retention_days
    now, require_researcher = views.clock, views.require_researcher

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
