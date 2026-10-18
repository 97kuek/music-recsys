"""Transparent preference evidence; no behavioral weights, decay or semantic guessing."""


def derive_preferences(histories, tracks, *, excluded=()):
    evidence = {}
    history = []
    for payload in histories:
        session_id = payload["session"]["session_id"]
        history.append(
            {
                "session_id": session_id,
                "initial_profile": payload["initial_profile"],
                "session_context": payload.get("session_context", {}),
                "events": payload["events"],
            }
        )
        for event in payload["events"]:
            item = event.get("item_id")
            if event["kind"] == "like" and item in tracks and item not in excluded:
                evidence.setdefault(item, []).append(
                    {
                        "session_id": session_id,
                        "request_id": event["request_id"],
                        "event_id": event["event_id"],
                    }
                )
    artists = {}
    for item in evidence:
        for artist in tracks[item].get("artists", ()):
            artists.setdefault(artist, set()).add(item)
    return {
        "method": "explicit-like-set-v1",
        "inferred_tracks": [
            {
                "item_id": item,
                "title": tracks[item].get("title"),
                "artists": tracks[item].get("artists", []),
                "source": "like_history",
                "evidence": evidence[item],
            }
            for item in sorted(evidence)
        ],
        "inferred_artists": [
            {"artist": a, "liked_item_ids": sorted(items)} for a, items in sorted(artists.items())
        ],
        "history": history,
    }
