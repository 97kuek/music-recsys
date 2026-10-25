"""Capture resolved constructor settings and immutable input identity."""

import inspect
import subprocess

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.provenance import source_hash
from src.paths import PROJECT_ROOT


def capture_training(model, arguments):
    """コンストラクタの解決済み引数と学習入力を、学習時点で記録する。"""
    root = PROJECT_ROOT
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
        "source_sha256": source_hash(),
        "lock_sha256": file_sha256(root / "uv.lock") if (root / "uv.lock").exists() else None,
    }
