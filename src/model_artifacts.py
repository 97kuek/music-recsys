"""全件モデルの検証付きNPY配信成果物。pickle・学習コードの実行は不要。"""

import inspect
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np

from src.ingestion.types import canonical_json, fingerprint
from src.listening_data import file_sha256
from src.serving import ModelSpec


def capture_training(model, arguments):
    """コンストラクタの解決済み引数と学習入力を、学習時点で記録する。"""
    from src.evaluation.benchmark import _source_sha256

    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
        )
        revision = result.stdout.strip() if result.returncode == 0 else None
    except FileNotFoundError:
        revision = None
    parameters = {
        key: arguments[key]
        for key in inspect.signature(type(model)).parameters
        if key not in {"learning_path", "progress", "cache_dir", "seed"}
    }
    model.training_provenance = {
        "parameters": parameters,
        "seed": arguments.get("seed", 0),
        "learning_sha256": file_sha256(arguments["learning_path"]),
        "git_revision": revision,
        "source_sha256": _source_sha256(),
        "lock_sha256": file_sha256(root / "uv.lock") if (root / "uv.lock").exists() else None,
    }


def export_model(model, destination, *, version_id):
    """学習済み全件モデルを保存し、manifest hashを返す。既存ディレクトリは拒否。"""
    from src.recommenders.full_als import FullALS
    from src.recommenders.full_bm25 import FullBM25
    from src.recommenders.full_bpr import FullBPR
    from src.recommenders.full_itemcf import FullItemCF
    from src.recommenders.full_popularity import FullPopularity

    classes = {
        FullALS: "als",
        FullBPR: "bpr",
        FullItemCF: "itemcf",
        FullBM25: "itemcf",
        FullPopularity: "popularity",
    }
    if type(model) not in classes or not hasattr(model, "training_provenance"):
        raise ValueError("export requires a full model with captured training provenance")
    algorithm = classes[type(model)]
    arrays = {"items": np.asarray(model.items, dtype=str), "plays": model.plays}
    if algorithm == "popularity":
        arrays["scores"] = model.scores
    elif algorithm == "itemcf":
        arrays.update(targets=model.targets, weights=model.weights)
    else:
        arrays.update(
            users=np.asarray(sorted(model.users, key=model.users.get), dtype=str),
            user_factors=model.user_factors,
            item_factors=model.item_factors,
        )
        if algorithm == "bpr":
            arrays["active_users"] = model.active_users
    provenance = model.training_provenance
    spec = ModelSpec(
        version_id,
        algorithm,
        canonical_json(provenance["parameters"]),
        provenance["learning_sha256"],
        fingerprint(arrays["items"].tolist()),
        provenance["source_sha256"],
        provenance["seed"],
    )
    from src.recommenders.deployed import DeployedRecommender

    DeployedRecommender(spec, arrays)  # 書込み前に形状・値・indexを検証
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        files = {}
        for name, array in arrays.items():
            path = destination / f"{name}.npy"
            np.save(path, array, allow_pickle=False)
            files[name] = {
                "sha256": file_sha256(path),
                "shape": list(array.shape),
                "dtype": str(array.dtype),
            }
        manifest = {
            "format": "music-recsys-model-v1",
            "spec": asdict(spec),
            "provenance": provenance,
            "files": files,
            "training_execution": getattr(model, "execution", None),
        }
        # manifestが最後の公開マーカー。途中ファイルのみではloadできない。
        temporary = destination / "manifest.partial"
        temporary.write_text(canonical_json(manifest), encoding="utf-8")
        temporary.rename(destination / "manifest.json")
    except BaseException:
        shutil.rmtree(destination)
        raise
    return file_sha256(destination / "manifest.json")


def load_model(directory, *, expected_manifest_sha256=None, als_fold_in=False):
    """hash検証後にread-only mmapで読込む。登録用(spec, recommender)を返す。"""
    from src.recommenders.deployed import DeployedRecommender

    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if (
        expected_manifest_sha256 is not None
        and file_sha256(manifest_path) != expected_manifest_sha256
    ):
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["format"] != "music-recsys-model-v1":
        raise ValueError("unsupported model format")
    spec = ModelSpec(**manifest["spec"])
    provenance = manifest["provenance"]
    if (
        spec.parameters != provenance["parameters"]
        or spec.seed != provenance["seed"]
        or spec.training_ref != provenance["learning_sha256"]
        or spec.implementation_ref != provenance["source_sha256"]
    ):
        raise ValueError("manifest provenance mismatch")
    required = {"items", "plays"} | {
        "popularity": {"scores"},
        "itemcf": {"targets", "weights"},
        "als": {"users", "item_factors", "user_factors"},
        "bpr": {"users", "item_factors", "user_factors", "active_users"},
    }.get(spec.algorithm, set())
    if (
        spec.algorithm not in {"popularity", "itemcf", "als", "bpr"}
        or set(manifest["files"]) != required
    ):
        raise ValueError("invalid model file set")
    arrays = {}
    for name, metadata in manifest["files"].items():
        path = directory / f"{name}.npy"
        if path.is_symlink() or file_sha256(path) != metadata["sha256"]:
            raise ValueError(f"artifact hash mismatch: {name}")
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        if list(array.shape) != metadata["shape"] or str(array.dtype) != metadata["dtype"]:
            raise ValueError(f"artifact schema mismatch: {name}")
        arrays[name] = array
    if fingerprint(arrays["items"].tolist()) != spec.catalog_ref:
        raise ValueError("catalog index mismatch")
    return spec, DeployedRecommender(spec, arrays, als_fold_in=als_fold_in)
