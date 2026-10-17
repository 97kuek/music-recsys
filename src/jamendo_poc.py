"""同じ100曲のJamendo照合・任意の実ブラウザ再生、アプリ設定の準備。"""

import argparse
import csv
import json
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from src.jamendo import JamendoClient, JamendoError, configure_credentials, match_record
from src.listening_data import file_sha256


def browser_check(rows):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<audio controls></audio>")
        for row in rows:
            if row.get("match") != "id_metadata_consistent" or not row.get("audio"):
                continue
            start = perf_counter()
            try:
                result = page.evaluate(
                    """async url => {
                  const a = document.querySelector('audio'); const events = [];
                  a.pause(); a.removeAttribute('src'); a.load();
                  for (const kind of ['playing','pause','ended','error']) a['on'+kind] = () => events.push(kind);
                  a.src = url;
                  try {
                    await Promise.race([a.play(), new Promise((_,reject) => setTimeout(() => reject(Error('timeout')),15000))]);
                    const begin = a.currentTime;
                    await new Promise(resolve => setTimeout(resolve, 1500));
                    const progressed = a.currentTime > begin + .1;
                    a.pause(); const stopped = a.paused;
                    return {success: progressed, stopped, events};
                  } catch { return {success: false, events, reason: 'browser_playback_error_or_timeout'}; }
                  finally { a.removeAttribute('src'); a.load(); }
                }""",
                    row["audio"],
                )
                row["browser"] = {**result, "seconds": perf_counter() - start}
            except Exception:
                row["browser"] = {"success": False, "reason": "browser_execution_error"}
        browser.close()


def run(sample, output, *, browser=False):
    if output.exists():
        raise FileExistsError(output)
    rows = list(csv.DictReader((sample / "tracks.csv").open()))
    if len(rows) != 100:
        raise ValueError("the original 100-track sample is required")
    client = JamendoClient()
    start = perf_counter()
    failure = None
    try:
        current = client.tracks([r["source_id"] for r in rows])
    except JamendoError as error:
        failure, current = error.code, {}
    observations = []
    records = []
    for row in rows:
        live = current.get(row["source_id"])
        match = None if failure else match_record(row, live)
        observations.append(
            {
                "item_id": row["item_id"],
                "metadata_received": None if failure else live is not None,
                "match": match,
                "failure": failure,
                "audio": live.get("audio") if live else None,
                "browser": None,
            }
        )
        records.append(
            {
                **row,
                "artists": [row["artist"]],
                "artwork": live.get("image")
                if live and match == "id_metadata_consistent"
                else None,
            }
        )
    api_seconds = perf_counter() - start if client.configured else None
    if browser:
        # License and matching are required before requesting any audio.
        for row in observations:
            source = next(r for r in rows if r["item_id"] == row["item_id"])
            live = current.get(source["source_id"], {})
            if not str(live.get("license_ccurl", "")).startswith(
                ("https://creativecommons.org/licenses/", "http://creativecommons.org/licenses/")
            ):
                row["audio"] = None
        if any(r["audio"] for r in observations):
            browser_check(observations)
    n = len(rows)
    tested = [r["browser"] for r in observations if r["browser"] is not None]
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "sample_sha256": file_sha256(sample / "tracks.csv"),
        "sample_size": n,
        "api_error": failure,
        "api_seconds": api_seconds,
        "metadata_rate": None if failure else len(current) / n,
        "id_metadata_consistency_rate": None
        if failure
        else sum(r["match"] == "id_metadata_consistent" for r in observations) / n,
        "stream_url_rate": None if failure else sum(bool(r["audio"]) for r in observations) / n,
        "browser_tested": len(tested),
        "browser_success_rate": sum(r["success"] for r in tested) / len(tested) if tested else None,
        "browser_success_over_sample": sum(r["success"] for r in tested) / n
        if len(tested) == n
        else None,
        "match_counts": dict(Counter(r["match"] or "unverified" for r in observations)),
        "observations": observations,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".jamendo-", dir=output.parent))
    (staging / "catalog.json").write_text(json.dumps(records, ensure_ascii=False))
    (staging / "features.json").write_bytes((sample / "features.json").read_bytes())
    original = json.loads((sample / "result.json").read_text())
    spec = original["model_spec"]
    spec["training_ref"] = file_sha256(staging / "features.json")
    spec["catalog_ref"] = file_sha256(staging / "catalog.json")
    spec["implementation_ref"] = "recommenders.tag_seed.TagSeedRecommender-v1"
    (staging / "spec.json").write_text(json.dumps(spec))
    config = {
        "mode": "jamendo",
        "models": [
            {
                "format": "content-tags",
                "path": "features.json",
                "sha256": file_sha256(staging / "features.json"),
                "spec": "spec.json",
                "spec_sha256": file_sha256(staging / "spec.json"),
            }
        ],
        "catalog": {"path": "catalog.json", "sha256": file_sha256(staging / "catalog.json")},
    }
    (staging / "config.json").write_text(json.dumps(config, indent=2))
    (staging / "result.json").write_text(json.dumps(result, indent=2))
    staging.rename(output)
    print(json.dumps({k: v for k, v in result.items() if k != "observations"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=Path("data/poc/mtg-jamendo-100"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    configure_credentials(args.env_file)
    run(args.sample, args.output, browser=args.browser)


if __name__ == "__main__":
    main()
