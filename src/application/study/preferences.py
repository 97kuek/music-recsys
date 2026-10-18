"""Consent-scoped history, editable preferences and session-only context."""

from src.domain.study.preferences import derive_preferences


class PreferenceService:
    def __init__(self, sessions):
        self.sessions = sessions

    def view(self, token):
        session = self.sessions.get(token)
        if not session.registered or not session.long_term_consent:
            raise PermissionError("長期嗜好の保存・再利用への同意が必要です")
        return self.for_participant(session.profile.user_id)

    def for_participant(self, participant_id):
        sessions = self.sessions
        participant = sessions.participants.get(participant_id, sessions.clock())
        profile = participant["profile"]
        same_catalog = profile.get("catalog_key") == sessions.resume_context["config_sha256"]
        profile = profile if same_catalog else {}
        histories = [
            p
            for p in sessions.store.participant_history(participant_id, sessions.clock())
            if p.get("resume", {}).get("context", {}).get("config_sha256")
            == sessions.resume_context["config_sha256"]
        ]
        derived = derive_preferences(
            histories, sessions.catalog.tracks, excluded=profile.get("excluded_items", ())
        )
        materialized = {
            key: derived[key] for key in ("method", "inferred_tracks", "inferred_artists")
        }
        materialized.update(
            computed_at=sessions.clock().isoformat(),
            catalog_ref=sessions.resume_context["config_sha256"],
        )
        sessions.store.update_participant_profile(
            participant_id,
            {**participant["profile"], "derived_profile": materialized},
            sessions.clock(),
        )

        def records(ids):
            return [
                {
                    "item_id": i,
                    "title": sessions.catalog.tracks[i].get("title"),
                    "artists": sessions.catalog.tracks[i].get("artists", []),
                }
                for i in ids
                if i in sessions.catalog.tracks
            ]

        return {
            **derived,
            "derived_profile": materialized,
            "user_tracks": records(profile.get("selected_item_ids", [])),
            "excluded_tracks": records(profile.get("excluded_items", [])),
            "user_settings": {
                "items": profile.get("selected_item_ids", []),
                "artists": profile.get("selected_artist_ids", []),
            },
            "excluded_items": profile.get("excluded_items", []),
            "consent": participant["profile"].get("long_term_permission"),
            "retain_until": participant["expires_at"],
        }

    def update(self, token, items, artists, excluded):
        session = self.sessions.get(token)
        self.view(token)
        catalog = self.sessions.catalog
        if (
            len(items) != len(set(items))
            or len(artists) != len(set(artists))
            or len(excluded) != len(set(excluded))
            or set(items) & set(excluded)
            or (set(items) | set(excluded)) - catalog.tracks.keys()
            or set(artists) - catalog.artists.keys()
        ):
            raise ValueError("プロフィールの曲・アーティストが不正です")
        self.sessions.participants.save_profile(
            session.profile.user_id,
            {
                "selected_item_ids": items,
                "selected_artist_ids": artists,
                "excluded_items": excluded,
            },
            self.sessions.resume_context["config_sha256"],
            self.sessions.clock(),
        )
        # A running comparison retains its frozen inputs; changes apply to the next session.
        return self.view(token)

    def set_context(self, token, choices, text):
        session = self.sessions.get(token)
        if session.finished:
            raise ValueError("終了済みです")
        if len(set(choices)) != len(choices) or set(choices) - {
            "calmer",
            "more_energy",
            "different_style",
        }:
            raise ValueError("unknown session request")
        if text and not session.log.session.allow_request_text:
            raise PermissionError("自由文保存への別同意が必要です")
        session.context = {
            "choices": choices,
            "text": text or None,
            "recorded_at": self.sessions.clock().isoformat(),
            "interpreted": False,
        }
        self.sessions.save(token, session)
        return session.context
