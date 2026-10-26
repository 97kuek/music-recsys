"""ItemCF・ALS・BPRの全件Validation探索と保存済み結果の比較。"""

import argparse
import fcntl
import json
from contextlib import ExitStack
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.grid import grid_variants
from src.infrastructure.research.results import read_result
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.evaluate import run
from src.paths import PROJECT_ROOT


def build_report(config, paths, compare=()):
    from src.bootstrap import create_factor_report

    return create_factor_report().write(config, paths, compare)


def build_grid_report(config_path):
    from src.bootstrap import create_itemcf_report

    return create_itemcf_report().write(config_path)


def run_grid(config_path, *, compare=()):
    config = load_study_config(config_path)
    if config.model == "itemcf" and compare:
        raise ValueError("ItemCF grid report does not accept --compare")
    planned = list(grid_variants(config))
    config.output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(study_lock(config))
        cache = None
        if config.model == "itemcf":
            cache = config.validation_dataset.parents[2] / "processed" / "itemcf"
            cache.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context((cache / ".lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path, raw in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if load_settings(path) != raw:
                    raise ValueError(f"Existing variant differs: {path}")
            else:
                atomic_json(path, raw)
            trial = load_study_config(path)
            if (trial.output / "result.json").exists():
                if config.model in {"als", "bpr"}:
                    _verify_implementation(trial)
                read_result(trial)
            else:
                run(path, model_cache=cache)
        if config.model == "itemcf":
            return build_grid_report(config_path)
        return build_report(config, [path for path, _ in planned], compare)


def _verify_implementation(trial):
    from src.infrastructure.provenance import source_hash

    saved = json.loads((trial.output / "config.json").read_text(encoding="utf-8"))
    lock = PROJECT_ROOT / "uv.lock"
    if saved.get("source_sha256") != source_hash() or saved.get(
        "dependency_lock_sha256"
    ) != file_sha256(lock):
        raise ValueError("Factor implementation or dependency lock changed; choose a new output")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--compare", action="append", default=[], type=Path)
    args = parser.parse_args()
    print(run_grid(args.config, compare=args.compare))


if __name__ == "__main__":
    main()
