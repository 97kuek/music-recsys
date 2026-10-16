"""ItemCFの固定Gridを全件評価 / 条件別設定と出力を分離し、学習近傍を再利用。"""

import argparse
import fcntl
import itertools
import json
from pathlib import Path

from music_recsys.configuration import load_settings
from music_recsys.experiment_search import load_study_config
from music_recsys.full_analysis import read_result
from music_recsys.full_evaluation import run
from music_recsys.grid_report import build_grid_report


def run_grid(config_path):
    base = load_study_config(config_path)
    raw = load_settings(base.path)
    if base.model != "itemcf" or set(base.grid) != {"neighbors", "similarity"}:
        raise ValueError("ItemCF requires neighbors and similarity grids")
    settings = list(itertools.product(base.grid["similarity"], base.grid["neighbors"]))
    if len(settings) > base.max_trials:
        raise ValueError("Grid exceeds max_trials")
    cache = base.validation_dataset.parents[2] / "processed" / "itemcf"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = base.path.parent / "itemcf"
        directory.mkdir(exist_ok=True)
        for similarity, neighbors in settings:
            name = f"itemcf-{similarity}-{neighbors}"
            variant = json.loads(json.dumps(raw))
            variant["model"]["parameters"] = dict(similarity=similarity, neighbors=neighbors)
            variant["search"] = dict(stage="A", seeds=[0], max_trials=1)
            e = variant["experiment"]
            e.update(name=name, output=str(base.output.with_name(name)))
            for key in ("validation_dataset", "test_dataset", "features"):
                e[key] = str(getattr(base, key))
            path = directory / f"{similarity}-{neighbors}.json"
            if path.exists() and json.loads(path.read_text()) != variant:
                raise ValueError(f"Existing variant differs: {path}")
            path.write_text(json.dumps(variant, ensure_ascii=False, indent=2) + "\n")
            config = load_study_config(path)
            if (config.output / "result.json").exists():
                read_result(config)
                print(f"Reusing completed {name}", flush=True)
            else:
                print(f"Starting {name}", flush=True)
                run(path, model_cache=cache)
        build_grid_report(config_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    run_grid(args.config)


if __name__ == "__main__":
    main()
