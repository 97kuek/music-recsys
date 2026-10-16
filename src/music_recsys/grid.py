"""ItemCF・ALS・BPRの全件Validation探索と保存済み結果の比較。"""

import argparse
import fcntl
import itertools
import json
from contextlib import ExitStack
from pathlib import Path

from music_recsys.configuration import load_settings
from music_recsys.experiment_search import load_study_config, study_lock
from music_recsys.experiment_store import atomic_json
from music_recsys.factor_report import build_report
from music_recsys.full_analysis import read_result
from music_recsys.full_evaluation import run
from music_recsys.itemcf_report import build_grid_report
from music_recsys.listening_data import file_sha256


def variants(config):
    if len(config.seeds) != 1:
        raise ValueError("Grid requires exactly one seed")
    if config.model == "itemcf" and set(config.grid) == {"similarity", "neighbors"}:
        conditions = [
            (f"{similarity}-{neighbors}", dict(similarity=similarity, neighbors=neighbors))
            for similarity, neighbors in itertools.product(
                config.grid["similarity"], config.grid["neighbors"]
            )
        ]
    elif config.model in {"als", "bpr"} and set(config.grid) == {"factors"}:
        from music_recsys.recommenders.factors import integer

        conditions = []
        for factors in config.grid["factors"]:
            integer(factors, "factors")
            conditions.append((str(factors), dict(factors=factors)))
    else:
        raise ValueError("Grid requires ItemCF similarity/neighbors or ALS/BPR factors")
    if len(conditions) > config.max_trials:
        raise ValueError("Grid exceeds max_trials")
    for suffix, parameters in conditions:
        raw = load_settings(config.path)
        raw["model"]["parameters"] = {**config.parameters, **parameters}
        raw["search"] = dict(stage="A", seeds=list(config.seeds), max_trials=1)
        # Preserve established ItemCF identities and paths for saved results.
        itemcf = config.model == "itemcf"
        name = f"itemcf-{suffix}" if itemcf else f"{config.name}-{suffix}"
        output = f"itemcf-{suffix}" if itemcf else f"{config.output.name}-{suffix}"
        raw["experiment"].update(name=name, output=str(config.output.with_name(output)))
        directory = "itemcf" if itemcf else config.path.stem
        yield config.path.parent / directory / f"{suffix}.json", raw


def run_grid(config_path, *, compare=()):
    config = load_study_config(config_path)
    if config.model == "itemcf" and compare:
        raise ValueError("ItemCF grid report does not accept --compare")
    planned = list(variants(config))
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
    from music_recsys.evaluation.benchmark import _source_sha256

    saved = json.loads((trial.output / "config.json").read_text(encoding="utf-8"))
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    if saved.get("source_sha256") != _source_sha256() or saved.get(
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
