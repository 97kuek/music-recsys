"""Read-only saved evaluation contracts shared by CLI and Notebook adapters."""

import json
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, read_bundle


def read_result(config):
    saved = json.loads((config.output / "config.json").read_text(encoding="utf-8"))
    if saved["settings"] != json.loads(json.dumps(asdict(config), default=str)):
        raise ValueError("保存結果と現在の設定が異なります")
    manifest, _ = read_bundle(config.validation_dataset)
    if saved["manifest"] != manifest or saved["features_sha256"] != file_sha256(config.features):
        raise ValueError("保存結果と現在の入力が異なります")
    return json.loads((config.output / "result.json").read_text(encoding="utf-8"))


def verify_artifacts(directory, artifacts):
    directory = Path(directory).resolve()
    for reference in artifacts.values():
        path = (directory / reference["path"]).resolve()
        if not path.is_relative_to(directory) or file_sha256(path) != reference["sha256"]:
            raise ValueError("Saved experiment artifact changed (checksum mismatch)")
