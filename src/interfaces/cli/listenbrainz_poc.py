"""Bounded ListenBrainz overlap audit; never infer recording identity from names."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from src.domain.catalog import normalize


def jamendo_id(url):
    """Accept only canonical Jamendo track URLs, not album or lookalike domains."""
    parsed = urlparse(str(url))
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {
        "jamendo.com",
        "www.jamendo.com",
    }:
        return None
    match = re.fullmatch(r"/(?:[a-z]{2}/)?track/(\d+)(?:/[^/]*)?/?", parsed.path)
    return match[1] if match else None


def audit(listens, catalog):
    by_id = {row["source_id"]: row for row in catalog}
    by_name = {}
    for row in catalog:
        key = (normalize(row["title"]).casefold(), normalize(row["artist"]).casefold())
        by_name.setdefault(key, []).append(row["source_id"])
    rows = []
    users = set()
    for index, listen in enumerate(listens):
        users.add(listen.get("user_name", "unknown"))
        metadata = listen["track_metadata"]
        info = metadata.get("additional_info", {})
        mapping = metadata.get("mbid_mapping", {})
        recording = mapping.get("recording_mbid") or info.get("recording_mbid")
        source = jamendo_id(info.get("origin_url", ""))
        key = (
            normalize(metadata.get("track_name", "")).casefold(),
            normalize(metadata.get("artist_name", "")).casefold(),
        )
        candidates = by_name.get(key, [])
        if source in by_id:
            status = "source_id_candidate"
            candidates = [source]
        elif candidates:
            status = "ambiguous_name_candidate"
        else:
            status = "unmatched"
        # Submitted URLs and MBID mappings are evidence, not reviewed crosswalks.
        rows.append(
            {
                "event_index": index,
                "listened_at": listen.get("listened_at"),
                "recording_mbid": recording,
                "jamendo_id": source,
                "candidate_ids": "|".join(candidates),
                "status": status,
            }
        )
    counts = Counter(row["status"] for row in rows)
    n = len(rows)
    result = {
        "events": n,
        "users": len(users),
        "catalog_tracks": len(catalog),
        "events_with_recording_mbid": sum(bool(r["recording_mbid"]) for r in rows),
        "unique_recording_mbids": len({r["recording_mbid"] for r in rows if r["recording_mbid"]}),
        "source_id_candidates": counts["source_id_candidate"],
        "ambiguous_name_candidates": counts["ambiguous_name_candidate"],
        "unmatched_events": counts["unmatched"],
        "candidate_rate": (n - counts["unmatched"]) / n if n else None,
        "verified_recording_match_rate": None,
        "false_match_rate": None,
        "playback_success_rate": None,
        "limitations": [
            "Convenience sample, not a population overlap estimate",
            "No reviewed MusicBrainz-to-Jamendo recording crosswalk",
            "No playback or collaborative-filtering training performed",
        ],
    }
    return rows, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listens", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # All parsing precedes output creation; existing results must remain untouched.
    payload = json.loads(args.listens.read_text())
    with args.catalog.open(newline="") as handle:
        catalog = list(csv.DictReader(handle))
    rows, result = audit(payload["payload"]["listens"], catalog)
    result["checked_at"] = datetime.now(UTC).isoformat()
    result["inputs_sha256"] = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in (("listens", args.listens), ("catalog", args.catalog))
    }
    args.output.mkdir(parents=True, exist_ok=False)
    args.output.chmod(0o700)
    with (args.output / "matches.csv").open("w", newline="") as handle:
        fields = [
            "event_index",
            "listened_at",
            "recording_mbid",
            "jamendo_id",
            "candidate_ids",
            "status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
