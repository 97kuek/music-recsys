"""権限不要のMusic4All取得・履歴コホート・listening-v2入力生成。"""

import argparse
import bz2
import csv
import fcntl
import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.domain.datasets import fingerprint
from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.datasets.temporal import TemporalConfig, prepare_temporal
from src.infrastructure.provenance import source_version
from src.paths import SOURCE_ROOT

FILES = {
    "id_tags_dict.tsv.bz2": (14817003, "9c1ca4a3e04a9696b9449ec54ac40cd5"),
    "userid_trackid_timestamp.tsv.bz2": (2211449511, "dfe82201036765f7463e6f3ce3d0f991"),
}


def log(message):
    print(message, flush=True)


def download(raw):
    """公開版を固定して再開取得。照合済みファイルだけを確定名にする。"""
    raw.mkdir(parents=True, exist_ok=True)
    record = raw / "record.json"
    if not record.exists():
        part = raw / "record.json.part"
        subprocess.run(
            [
                "curl",
                "-fLsS",
                "--retry",
                "3",
                "-o",
                str(part),
                "https://zenodo.org/api/records/15394646",
            ],
            check=True,
        )
        json.loads(part.read_text())
        part.rename(record)
    metadata = {f["key"]: f for f in json.loads(record.read_text())["files"]}
    for name, (size, md5) in FILES.items():
        if (metadata[name]["size"], metadata[name]["checksum"]) != (size, "md5:" + md5):
            raise ValueError("配布情報が固定した版と異なります")
        final, part = raw / name, raw / (name + ".part")
        if not final.exists() and (not part.exists() or part.stat().st_size < size):
            log(f"Downloading {name} ({size:,} bytes; resumable)")
            subprocess.run(
                [
                    "curl",
                    "-fLsS",
                    "--retry",
                    "5",
                    "--connect-timeout",
                    "30",
                    "--speed-limit",
                    "1024",
                    "--speed-time",
                    "120",
                    "-C",
                    "-",
                    "-o",
                    str(part),
                    f"https://zenodo.org/records/15394646/files/{name}?download=1",
                ],
                check=True,
            )
        candidate = final if final.exists() else part
        with candidate.open("rb") as stream:
            digest = hashlib.file_digest(stream, "md5").hexdigest()
        if candidate.stat().st_size != size or digest != md5:
            raise ValueError(f"サイズまたはMD5不一致: {name}")
        if candidate == part:
            part.rename(final)
        log(f"Verified {name}")


def verify_prepared(output):
    """公開済み一式の全ファイルとbundleの内容を再検証する。"""
    ready = json.loads((output / "ready.json").read_text())
    for name, expected in ready["files"].items():
        if file_sha256(output / name) != expected:
            raise ValueError(f"保存物のchecksum不一致: {name}")
    for phase in ("validation", "test"):
        validate_bundle(output / phase)
    return ready


def prepare_files(cache, tags, output, *, users=None, source=None):
    """Trainの元ID順コホートを抽出。DBに登録せず同じexporterを使用する。"""
    import duckdb

    from src.infrastructure.datasets.features import write_features
    from src.infrastructure.datasets.music4all_adapter import parse_tags
    from src.interfaces.cli.listening_export import export_listening_phase

    if users is not None and (type(users) is not int or users <= 0):
        raise ValueError("users must be positive")
    temporal = TemporalConfig()
    identity = {
        "source": source or {},
        "cache_sha256": file_sha256(cache),
        "tags_sha256": file_sha256(tags),
        "users": users,
        "selection": "all-train-observed"
        if users is None
        else "train-observed-source-id-ascending",
        "temporal": asdict(temporal),
        "code": source_version(
            SOURCE_ROOT,
            (
                "infrastructure/provenance.py",
                "domain/datasets.py",
                "domain/recommendation/features.py",
                "interfaces/cli/lab_data.py",
                "infrastructure/datasets/temporal.py",
                "interfaces/cli/listening_export.py",
                "infrastructure/datasets/listening_data.py",
                "infrastructure/datasets/features.py",
                "infrastructure/datasets/music4all_adapter.py",
            ),
        ),
    }
    if output.exists():
        ready = verify_prepared(output)
        if ready["identity"] != identity:
            raise ValueError("入力・処理条件が異なります。別の出力先を指定してください")
        return ready
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lab-prepare-", dir=output.parent) as directory:
        work = Path(directory) / "bundle"
        work.mkdir()
        events = work / "events.parquet"
        with duckdb.connect(
            config={
                "memory_limit": "2GB",
                "threads": 2,
                "temp_directory": str(work / "spill"),
            }
        ) as c:
            c.read_parquet(str(cache)).create_view("all_events")
            c.execute(
                "CREATE TEMP TABLE cohort AS SELECT DISTINCT user_id FROM all_events "
                "WHERE event_time < ? ORDER BY user_id" + (" LIMIT ?" if users is not None else ""),
                [datetime.fromisoformat(temporal.train_end)]
                + ([users] if users is not None else []),
            )
            selected_users = c.execute("SELECT count(*) FROM cohort").fetchone()[0]
            log(f"Selected {selected_users:,} train-observed users")
            if not selected_users or (users is not None and selected_users != users):
                raise ValueError("Train観測ユーザーが指定人数より少ないです")
            c.execute(
                "COPY (SELECT e.* FROM all_events e JOIN cohort USING(user_id) "
                "ORDER BY user_id,item_id,event_time) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(events)],
            )
            items = {
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT item_id FROM read_parquet(?)", [str(events)]
                ).fetchall()
            }
        snapshot = "file-" + fingerprint(identity)
        result = prepare_temporal(events, work / "source", temporal, provenance={}, progress=log)
        split = "file-" + fingerprint(
            {role: file_sha256(result.files[role]) for role in ("train", "validation", "test")}
        )
        provenance = {
            "dataset_key": "music4all-onion",
            "version_label": "v2",
            "snapshot_id": snapshot,
            "split_id": split,
            "storage": "files-only",
            "preparation": identity,
        }
        for phase in ("validation", "test"):
            export_listening_phase(
                result.files,
                work / phase,
                temporal,
                phase=phase,
                provenance=provenance,
            )
        payloads, features = {}, {}
        with bz2.open(tags, "rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != ["id", "(tag, weight)"]:
                raise ValueError("unexpected tag columns")
            for row in reader:
                payload = parse_tags(row["(tag, weight)"])["tags"]
                item = row["id"]
                if item in payloads and payloads[item] != payload:
                    raise ValueError("conflicting duplicate tags")
                payloads[item] = payload
                if item in items and payload:
                    features[item] = payload
        write_features(
            work / "features.json",
            dataset_id="music4all-onion",
            snapshot_id=snapshot,
            features=features,
            source={"tags_sha256": identity["tags_sha256"]},
        )
        ready = {
            "identity": identity,
            "snapshot_id": snapshot,
            "split_id": split,
            "users": selected_users,
            "tracks": len(items),
            "evaluation_executed": False,
            "tags_missing": len(items - payloads.keys()),
            "tags_empty": sum(not payloads[i] for i in items & payloads.keys()),
            "files": {
                str(p.relative_to(work)): file_sha256(p)
                for p in sorted(work.rglob("*"))
                if p.is_file()
            },
        }
        (work / "ready.json").write_text(json.dumps(ready, indent=2) + "\n")
        verify_prepared(work)
        work.rename(output)
    return ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/raw/music4all-onion-v2"))
    parser.add_argument(
        "--cache", type=Path, default=Path("data/processed/music4all-onion-v2/events.parquet")
    )
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/music4all"))
    parser.add_argument("--users", type=int, default=None, help="省略時は学習履歴のある全ユーザー")
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    args.raw.mkdir(parents=True, exist_ok=True)
    with (args.raw / "prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        download(args.raw)
        if args.download_only:
            return
        from src.infrastructure.datasets.music4all import convert_events

        args.cache.parent.mkdir(parents=True, exist_ok=True)
        if not args.cache.exists() and shutil.disk_usage(args.cache.parent).free < 15 * 1024**3:
            raise RuntimeError("Parquet変換前の空きが15GiB未満です。保存先を確認してください")
        log("Converting/verifying full event cache")
        manifest = convert_events(
            args.raw / "userid_trackid_timestamp.tsv.bz2",
            args.cache,
            progress=log,
        )
        ready = prepare_files(
            args.cache,
            args.raw / "id_tags_dict.tsv.bz2",
            args.output,
            users=args.users,
            source={
                "record": "https://zenodo.org/records/15394646",
                "cache": manifest,
                "lock_sha256": file_sha256(Path("uv.lock")),
            },
        )
        log(f"READY: {ready['users']} users, {ready['tracks']} tracks; evaluation not executed")


if __name__ == "__main__":
    main()
