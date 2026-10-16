"""ALS / BPR factorsのValidation比較 / 保存済み手法も同条件で比較。"""

import argparse
import json
from pathlib import Path

from music_recsys.configuration import load_settings
from music_recsys.experiment_search import load_study_config, study_lock
from music_recsys.experiment_store import atomic_json
from music_recsys.factor_report import build_report
from music_recsys.full_analysis import read_result
from music_recsys.full_evaluation import run
from music_recsys.listening_data import file_sha256


def variants(config):
    if (
        config.model not in {"als", "bpr"}
        or set(config.grid) != {"factors"}
        or len(config.seeds) != 1
    ):
        raise ValueError("Factor grid requires factors and exactly one seed")
    if len(config.grid["factors"]) > config.max_trials:
        raise ValueError("Grid exceeds max_trials")
    from music_recsys.recommenders.factors import integer

    for factors in config.grid["factors"]:
        integer(factors, "factors")
        raw = load_settings(config.path)
        raw["model"]["parameters"] = {**config.parameters, "factors": factors}
        raw["search"] = dict(stage="A", seeds=list(config.seeds), max_trials=1)
        raw["experiment"].update(
            name=f"{config.name}-{factors}",
            output=str(config.output.with_name(f"{config.output.name}-{factors}")),
            **{
                key: str(getattr(config, key))
                for key in ("validation_dataset", "test_dataset", "features")
            },
        )
        path = config.path.parent / config.path.stem / f"{factors}.json"
        yield path, raw


def run_grid(config_path, *, compare=()):
    config = load_study_config(config_path)
    planned = list(variants(config))
    config.output.parent.mkdir(parents=True, exist_ok=True)
    with study_lock(config):
        for path, raw in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if json.loads(path.read_text(encoding="utf-8")) != raw:
                    raise ValueError(f"Existing variant differs: {path}")
            else:
                atomic_json(path, raw)
            trial = load_study_config(path)
            if (trial.output / "result.json").exists():
                from music_recsys.evaluation.benchmark import _source_sha256

                saved = json.loads((trial.output / "config.json").read_text(encoding="utf-8"))
                lock_path = Path(__file__).resolve().parents[2] / "uv.lock"
                if saved.get("source_sha256") != _source_sha256() or saved.get(
                    "dependency_lock_sha256"
                ) != file_sha256(lock_path):
                    raise ValueError(
                        "Factor implementation or dependency lock changed; choose a new output"
                    )
                read_result(trial)
            else:
                run(path)
        return build_report(config, [path for path, _ in planned], compare)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--compare", action="append", default=[], type=Path)
    args = parser.parse_args()
    print(run_grid(args.config, compare=args.compare))


if __name__ == "__main__":
    main()
