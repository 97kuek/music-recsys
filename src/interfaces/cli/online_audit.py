"""配信設定と明示した音源ルートを読み取り監査。再学習・配信・設定選択はしない。"""

import argparse
import json
import resource
import socket
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from src.domain.recommendation.profiles import InitialProfile
from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.models.artifacts import load_model

AUDIO = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}


def audit(config_path, roots):
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text())
    catalog_path = config_path.parent / config["catalog"]["path"]
    if file_sha256(catalog_path) != config["catalog"]["sha256"]:
        raise ValueError("catalog checksum mismatch")
    tracks = json.loads(catalog_path.read_text())
    ids = {row["item_id"] for row in tracks}
    files = {}
    for root in roots:
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("audio root must be an existing directory")
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in AUDIO:
                files[str(path.resolve())] = path
    matching = {p.stem for p in files.values()} & ids
    models = []
    for entry in config["models"]:
        start = perf_counter()
        spec, model = load_model(
            config_path.parent / entry["path"],
            expected_manifest_sha256=entry["sha256"],
            als_fold_in=entry.get("als_fold_in", False),
        )
        load_seconds = perf_counter() - start
        candidates = tuple(i for i in model.index if i in ids)
        seeds = tuple(candidates[:3])
        times = []
        for i in range(23):
            request = InitialProfile("audit-new-user", seeds).to_request(
                f"audit-{i}", candidates, top_k=20
            )
            start = perf_counter()
            response = model.recommend(request)
            elapsed = (perf_counter() - start) * 1000
            if i >= 3:
                times.append(elapsed)
        models.append(
            {
                "version": spec.version_id,
                "algorithm": spec.algorithm,
                "parameters": spec.parameters,
                "load_seconds": load_seconds,
                "cold_start_route": model.route(request),
                "latency_median_ms": statistics.median(times),
                "latency_max_ms": max(times),
                "returned": len(response.item_ids),
                "candidate_count": len(candidates),
                "manifest_sha256": entry["sha256"],
            }
        )
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "host": socket.gethostname(),
        "config_sha256": file_sha256(config_path),
        "catalog_tracks": len(ids),
        "named_tracks": sum(bool(t.get("title") and t.get("artists")) for t in tracks),
        "scanned_roots": [str(Path(p).resolve()) for p in roots],
        "audio_files": len(files),
        "audio_bytes": sum(p.stat().st_size for p in files.values()),
        "formats": dict(Counter(p.suffix.lower() for p in files.values())),
        "filename_id_matches": len(matching),
        "filename_id_match_rate": len(matching) / len(ids) if ids else None,
        "configured_audio": len(config.get("audio", {})),
        "web_distribution_permission": "unverified",
        "audio_duration_seconds": None,
        "models": models,
        "missing_algorithms": sorted(
            {"popularity", "itemcf", "als", "bpr"} - {m["algorithm"] for m in models}
        ),
        "peak_process_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "measurement": "sequential model loading; 3 warmup + 20 requests; first 3 catalog IDs as synthetic profile only; no interaction training; not end-to-end latency",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--audio-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.config, args.audio_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "models"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
