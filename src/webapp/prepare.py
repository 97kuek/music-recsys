"""アプリ用データの明示準備。--demoと--music4allは混同・自動切替しない。"""

import argparse
import json
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from src.ingestion.types import canonical_json
from src.listening_data import file_sha256
from src.model_artifacts import export_model
from src.recommenders.full_als import FullALS
from src.recommenders.full_bpr import FullBPR
from src.recommenders.full_itemcf import FullItemCF
from src.recommenders.full_popularity import FullPopularity


def write(path, value):
    path.write_text(canonical_json(value), encoding="utf-8")


def demo(destination):
    import pyarrow as pa
    import pyarrow.parquet as pq

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    tracks = []
    audio = {}
    titles = (
        "薄明のループ",
        "窓辺のリズム",
        "夜の輪郭",
        "朝の余白",
        "木漏れ日の拍子",
        "雨あがり",
        "遠い灯り",
        "青い波紋",
        "街の残響",
        "星の瞬き",
        "ゆるやかな午後",
        "帰り道",
    )
    for i, title in enumerate(titles):
        item = f"sample-{i:02}"
        tracks.append(
            {
                "item_id": item,
                "title": title,
                "artists": [("Ambient Lab", "Soft Pulse", "Evening Keys")[i % 3]],
            }
        )
        rate = 22050
        time = np.arange(rate * 8) / rate
        # 原曲・録音のコピーを使わない、独自に合成した短い音型。
        root = 130.81 * 2 ** ((i % 12) / 12)
        notes = np.array([0, 4, 7, 12, 7, 4, 2, 0])
        freq = root * 2 ** (notes[np.minimum(time.astype(int), 7)] / 12)
        phase = np.cumsum(freq) / rate
        envelope = np.sin(np.pi * (time % 1)) ** 2
        samples = 0.16 * envelope * (np.sin(2 * np.pi * phase) + 0.3 * np.sin(4 * np.pi * phase))
        path = destination / f"{item}.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            stream.writeframes((samples * 32767).astype("<i2").tobytes())
        audio[item] = {
            "path": path.name,
            "sha256": file_sha256(path),
            "license_ref": "original-procedural-sample-CC0-1.0",
            "sample": True,
            "expires_at": (datetime.now(UTC) + timedelta(days=3650)).isoformat(),
        }
    learning = destination / "learning.parquet"
    records = [
        dict(user_id=f"demo-user-{u:02}", item_id=f"sample-{i:02}", play_count=1 + (u + i) % 4)
        for u in range(18)
        for i in range(12)
        if i % 3 == u % 3 or i == u % 12 or i == (u + 1) % 12
    ]
    pq.write_table(pa.Table.from_pylist(records), learning)
    models = [
        FullPopularity(learning),
        FullItemCF(learning, neighbors=5, progress=lambda _: None),
        FullALS(learning, factors=4, iterations=3, progress=lambda _: None),
        FullBPR(learning, factors=4, iterations=3, progress=lambda _: None),
    ]
    entries = []
    for name, model in zip(("popularity", "itemcf", "als", "bpr"), models, strict=True):
        version = f"demo-{name}"
        digest = export_model(model, destination / version, version_id=version)
        entries.append({"path": version, "sha256": digest, "als_fold_in": name == "als"})
    write(destination / "catalog.json", tracks)
    write(
        destination / "config.json",
        {
            "mode": "demo",
            "selection": "comparison-only",
            "models": entries,
            "catalog": {
                "path": "catalog.json",
                "sha256": file_sha256(destination / "catalog.json"),
            },
            "audio": audio,
            "comparison": [],
        },
    )
    (destination / "LICENSE.txt").write_text(
        "Generated procedural demonstration audio: CC0-1.0. No existing recording or composition was sampled. Synthetic interactions and invented titles; not Music4All.\n"
    )
    return destination / "config.json"


def validation_comparison(experiments, learning_sha):
    """既存Validationの名前を正解集合に合わせて表示。総合指標を捏造しない。"""
    rows = []
    for path in sorted(Path(experiments).glob("*/result.json")):
        result = json.loads(path.read_text())
        if (
            result.get("phase") != "validation"
            or result.get("status") != "succeeded"
            or "accuracy" not in result
        ):
            continue
        saved = json.loads((path.parent / "config.json").read_text())
        if (
            saved["manifest"]["phase"] != "validation"
            or saved["manifest"]["files"]["learning"]["sha256"] != learning_sha
        ):
            raise ValueError("Validation comparison input mismatch")
        rows.append(
            {
                "condition": path.parent.name,
                "model": result["model"],
                "parameters": result.get("model_parameters"),
                "source_sha256": file_sha256(path),
                "accuracy": result["accuracy"],
                "diversity": result["diversity"],
                "efficiency": result["efficiency"],
                "general_binary_ndcg": None,
            }
        )
    return rows


def music4all(destination, bundle, cache, experiments):
    destination, bundle, cache = Path(destination), Path(bundle), Path(cache)
    manifest = json.loads((bundle / "manifest.json").read_text())
    learning = bundle / "learning.parquet"
    if (
        manifest["phase"] != "validation"
        or file_sha256(learning) != manifest["files"]["learning"]["sha256"]
    ):
        raise ValueError("verified Validation learning input required")
    # 存在確認を先に行い、キャッシュ欠落時に重いItemCF学習を始めない。
    for similarity in ("cosine", "jaccard"):
        if not (cache / f"{similarity}-100.npz").is_file():
            raise ValueError("ItemCF cache missing; no automatic retraining")
    comparison = validation_comparison(experiments, manifest["files"]["learning"]["sha256"])
    destination.mkdir(parents=True, exist_ok=False)
    entries = []
    items = None
    for basis in ("play_count", "user_count"):
        model = FullPopularity(learning, basis=basis)
        version = f"music4all-popularity-{basis}"
        entries.append(
            {
                "path": version,
                "sha256": export_model(model, destination / version, version_id=version),
            }
        )
        items = model.items
    for similarity in ("cosine", "jaccard"):
        for neighbors in (20, 50, 100):
            model = FullItemCF(
                learning, similarity=similarity, neighbors=neighbors, cache_dir=cache
            )
            if not model.cache_hit:
                raise ValueError("cache-only preparation expected")
            version = f"music4all-itemcf-{similarity}-{neighbors}"
            entries.append(
                {
                    "path": version,
                    "sha256": export_model(model, destination / version, version_id=version),
                }
            )
    # 手元の履歴・タグには曲名/artistがない。実曲の名前を作って埋めない。
    write(
        destination / "catalog.json",
        [{"item_id": str(item), "title": None, "artists": []} for item in items],
    )
    write(
        destination / "config.json",
        {
            "mode": "music4all",
            "selection": "comparison-only",
            "models": entries,
            "catalog": {
                "path": "catalog.json",
                "sha256": file_sha256(destination / "catalog.json"),
            },
            "audio": {},
            "comparison": comparison,
        },
    )
    return destination / "config.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true")
    mode.add_argument("--music4all", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--experiments", type=Path)
    args = parser.parse_args()
    if args.demo:
        print(demo(args.output))
    else:
        if not all((args.bundle, args.cache, args.experiments)):
            parser.error("--music4all requires --bundle, --cache, --experiments")
        print(music4all(args.output, args.bundle, args.cache, args.experiments))


if __name__ == "__main__":
    main()
