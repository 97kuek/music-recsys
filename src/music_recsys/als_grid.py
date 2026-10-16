"""ALS factorsのValidation比較 / 保存済みPopularity・ItemCFも同条件で比較。"""

import argparse
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from music_recsys.experiment_search import load_study_config, study_lock
from music_recsys.experiment_store import atomic_json
from music_recsys.full_analysis import read_result
from music_recsys.full_evaluation import run
from music_recsys.listening_data import file_sha256


def variants(config):
    if config.model != "als" or set(config.grid) != {"factors"} or len(config.seeds) != 1:
        raise ValueError("ALS grid requires factors and exactly one seed")
    if len(config.grid["factors"]) > config.max_trials:
        raise ValueError("Grid exceeds max_trials")
    from music_recsys.recommenders.factors import integer

    for factors in config.grid["factors"]:
        integer(factors, "factors")
        raw = json.loads(config.path.read_text())
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
                if json.loads(path.read_text()) != raw:
                    raise ValueError(f"Existing variant differs: {path}")
            else:
                atomic_json(path, raw)
            trial = load_study_config(path)
            if (trial.output / "result.json").exists():
                from music_recsys.evaluation.benchmark import _source_sha256

                saved = json.loads((trial.output / "config.json").read_text())
                lock_path = Path(__file__).resolve().parents[2] / "uv.lock"
                if saved.get("source_sha256") != _source_sha256() or saved.get(
                    "dependency_lock_sha256"
                ) != file_sha256(lock_path):
                    raise ValueError(
                        "ALS implementation or dependency lock changed; choose a new output"
                    )
                read_result(trial)
            else:
                run(path)
        return build_report(config, [path for path, _ in planned], compare)


def build_report(config, paths, compare=()):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from music_recsys.evaluation_figures import ACCURACY_NAMES

    expanded = list(paths)
    for path in compare:
        trial = load_study_config(path)
        result = read_result(trial)
        if result.get("format") == "music-recsys-grid-comparison-v1":
            for source in result["sources"]:
                if file_sha256(source["result"]) != source["result_sha256"]:
                    raise ValueError("Comparison source changed")
                expanded.append(Path(source["config"]))
        else:
            expanded.append(path)
    rows, sources, reference = [], [], None
    for path in dict.fromkeys(Path(p).resolve() for p in expanded):
        trial = load_study_config(path)
        result = read_result(trial)
        saved = json.loads((trial.output / "config.json").read_text())
        identity = {key: saved[key] for key in ("manifest", "features_sha256", "population")}
        identity["evaluation_definitions"] = {
            key: value
            for key, value in result["definitions"].items()
            if key not in {"ranking", "latency"}
        }
        if reference is None:
            reference = identity
        if identity != reference or trial.evaluation != config.evaluation:
            raise ValueError("Comparison input or evaluation conditions differ")
        if result.get("status") != "succeeded" or result.get("phase") != "validation":
            raise ValueError("Comparison requires successful Validation")
        for artifact in result["artifacts"].values():
            if file_sha256(trial.output / artifact["path"]) != artifact["sha256"]:
                raise ValueError("Comparison artifact changed")
        sources.append(
            dict(
                config=str(path),
                result=str(trial.output / "result.json"),
                result_sha256=file_sha256(trial.output / "result.json"),
                source_sha256=saved.get("source_sha256"),
            )
        )
        label = f"{trial.model} / " + ", ".join(
            f"{k}={v}"
            for k, v in trial.parameters.items()
            if k in {"factors", "similarity", "neighbors", "basis"}
        )
        condition = dict(
            experiment=label,
            model=trial.model,
            factors=trial.parameters.get("factors"),
            seed=trial.seeds[0],
            parameters=json.dumps(trial.parameters, sort_keys=True),
        )
        for score in result["accuracy"]:
            if score["window_days"] == config.evaluation.primary_window_days and score[
                "day_weight"
            ] in (None, config.evaluation.primary_day_weight):
                rows.append(dict(**condition, **score))
        for score in result["diversity"]:
            for metric in ("ild", "catalog_coverage", "novelty"):
                rows.append(
                    dict(
                        **condition,
                        task=score["task"],
                        k=score["k"],
                        metric=metric,
                        mean=score[metric],
                    )
                )
        for metric, value in result["efficiency"].items():
            if metric != "model":
                rows.append(dict(**condition, task="all", k=None, metric=metric, mean=value))
    if (config.output / "result.json").exists():
        prior = read_result(config)
        if prior["sources"] != sources:
            raise ValueError("Existing report differs; choose a new output")
        return config.output
    frame = pd.DataFrame(rows)
    # Stage figures and publish the result marker only after all artifacts are ready.
    with tempfile.TemporaryDirectory(prefix="als-report-", dir=config.output.parent) as temporary:
        output = Path(temporary) / "report"
        output.mkdir()
        frame.to_csv(output / "metrics.csv", index=False, encoding="utf-8-sig")
        names = []
        for (task, metric), values in frame.groupby(["task", "metric"], sort=False):
            if not values["mean"].notna().any():
                continue
            name, title = ACCURACY_NAMES.get(
                (task, metric), (f"{metric}_{task}", f"{metric} · {task}")
            )
            for suffix, subset in (("", values), ("_factors", values[values.model == "als"])):
                if not subset["mean"].notna().any():
                    continue
                fig, ax = plt.subplots(figsize=(9, 5))
                if task == "all":
                    ax.bar(subset.experiment, subset["mean"])
                    ax.tick_params(axis="x", labelrotation=30)
                else:
                    for label, group in subset.groupby("experiment", sort=False):
                        group = group.sort_values("k")
                        ax.plot(group.k, group["mean"], marker="o", markersize=3, label=label)
                    ax.set(xlabel="K", xticks=sorted(subset.k.unique()))
                    ax.legend(fontsize=7)
                ax.set(ylabel=title, title=title, ylim=(0, None))
                ax.grid(alpha=0.2)
                fig.tight_layout()
                filename = f"{name}{suffix}.png"
                fig.savefig(output / filename, dpi=160)
                plt.close(fig)
                names.append(filename)
        atomic_json(
            output / "config.json",
            dict(
                format="music-recsys-full-config-v1",
                settings=json.loads(json.dumps(asdict(config), default=str)),
                **reference,
            ),
        )
        atomic_json(
            output / "result.json",
            dict(
                format="music-recsys-grid-comparison-v1",
                status="succeeded",
                selection="not_selected",
                model="als",
                sources=sources,
                population=reference["population"],
                figures=names,
                metrics=json.loads(frame.to_json(orient="records", double_precision=15)),
            ),
        )
        (output / "run.log").write_text(
            "SUCCEEDED: verified Validation comparisons; no setting selected\n"
        )
        # study_lock creates the output directory, which must contain no report yet.
        config.output.mkdir(parents=True, exist_ok=True)
        for path in output.iterdir():
            if path.name != "result.json":
                os.replace(path, config.output / path.name)
        os.replace(output / "result.json", config.output / "result.json")
    return config.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--compare", action="append", default=[], type=Path)
    args = parser.parse_args()
    print(run_grid(args.config, compare=args.compare))


if __name__ == "__main__":
    main()
