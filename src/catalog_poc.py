"""MTG-Jamendoのメタデータだけを使う、オフラインのカタログPoC。"""

import argparse
import csv
import hashlib
import json
import random
import re
import resource
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.catalog import (
    CatalogTrack,
    PlaybackAvailability,
    TrackCatalog,
    TrackRef,
    TrackSource,
    normalize,
)
from src.recommenders.tag_seed import TagSeedRecommender
from src.serving import FixedStrategy, ModelSpec, RecommendationService
from src.types import RecommendationRequest


class MetadataCatalogAdapter:
    """静的メタデータ参照。URLが存在しても再生可とは判定しない。"""

    def __init__(self, tracks):
        self.sources = {s.ref: s for t in tracks for s in t.sources}

    def lookup(self, ref):
        return self.sources.get(ref)

    def availability(self, context):
        now = datetime.now(UTC)
        return PlaybackAvailability(context, "unknown", now, now + timedelta(seconds=30))


def run(inputs, output, count=100, seed=42):
    if output.exists():
        raise FileExistsError(output)
    if not 100 <= count <= 500:
        raise ValueError("sample size must be 100..500")
    hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.iterdir() if p.is_file()
    }
    with (inputs / "raw.meta.tsv").open() as f:
        metadata = {r["TRACK_ID"]: r for r in csv.DictReader(f, delimiter="\t")}
    with (inputs / "tags.tsv").open() as f:
        rows = list(csv.reader(f, delimiter="\t"))[1:]
    eligible = sorted((r for r in rows if r[0] in metadata and len(r) > 5), key=lambda r: r[0])
    sample = random.Random(seed).sample(eligible, count)
    revision = hashes["raw.meta.tsv"]
    licenses = (inputs / "audio_licenses.txt").read_text()
    license_map = dict(
        re.findall(
            r"(?:^|\n)(\d+/\d+\.mp3)\n[^\n]*\n[^\n]*?: (https?://creativecommons.org/[^\s]+)",
            licenses,
        )
    )
    tracks, records, features = [], [], {}
    for r in sample:
        m = metadata[r[0]]
        source_id = str(int(r[0].removeprefix("track_")))
        item = f"mtg-jamendo:{source_id}"
        url_match = m["URL"].rstrip("/").split("/")[-1] == source_id
        tracks.append(
            CatalogTrack(
                item,
                (
                    TrackSource(
                        TrackRef("jamendo", source_id),
                        revision,
                        normalize(m["TRACK_NAME"]),
                        (normalize(m["ARTIST_NAME"]),),
                    ),
                ),
            )
        )
        features[item] = {tag: 1.0 for tag in r[5:] if tag}
        records.append(
            {
                "item_id": item,
                "title": normalize(m["TRACK_NAME"]),
                "artist": normalize(m["ARTIST_NAME"]),
                "source_id": source_id,
                "source_url": m["URL"],
                "dataset_id_url_match": url_match,
                "license_url": license_map.get(r[3]),
                "live_recording_match": None,
                "playable": None,
                "failure_reason": "live_api_not_verified",
            }
        )
    catalog = TrackCatalog(tuple(tracks))
    adapter = MetadataCatalogAdapter(tracks)
    assert all(
        catalog.resolve(t.sources[0].ref) == t.item_id and adapter.lookup(t.sources[0].ref)
        for t in tracks
    )
    spec = ModelSpec(
        "mtg-tags-poc",
        "content-tags",
        json.dumps({"normalization": "l2", "positive_scores_only": True}),
        revision,
        revision,
        "catalog_poc-v1",
        seed,
    )
    service = RecommendationService(((spec, TagSeedRecommender(features)),))
    examples = []
    for track in tracks:
        req = RecommendationRequest(
            track.item_id, "poc-seed-profile", (track.item_id,), tuple(features), 5
        )
        execution = service.recommend(req, FixedStrategy(spec.version_id))
        examples.append(
            {
                "seed": track.item_id,
                "recommended": execution.response.item_ids,
                "seconds": execution.inference_seconds,
            }
        )
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "population": len(eligible),
        "sample_size": count,
        "seed": seed,
        "sampling": "uniform without replacement over sorted tagged IDs with metadata",
        "input_sha256": hashes,
        "dataset_id_url_matches": sum(r["dataset_id_url_match"] for r in records),
        "live_match_rate": None,
        "playable_rate": None,
        "playback_checked": 0,
        "licensed_metadata_rows": sum(bool(r["license_url"]) for r in records),
        "profiles_with_recommendations": sum(bool(r["recommended"]) for r in examples),
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "model_spec": asdict(spec),
        "recommendations": examples,
    }
    destination = output
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=".catalog-poc-", dir=destination.parent))
    with (output / "tracks.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    (output / "catalog.json").write_text(
        json.dumps([asdict(t) for t in tracks], ensure_ascii=False, indent=2)
    )
    (output / "features.json").write_text(json.dumps(features, ensure_ascii=False))
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    output.rename(destination)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"recommendations", "model_spec", "input_sha256"}
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    run(args.inputs, args.output, args.count, args.seed)
