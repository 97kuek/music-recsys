"""Sequential Validation parameter diagnostics, with immutable trials and cited controls."""

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.comparison import staged_report
from src.paths import PROJECT_ROOT


def condition_name(model, parameters, seed):
    if model == "ease":
        return f"l{parameters['regularization']:g}"
    if model == "als":
        return (
            f"f{parameters['factors']}-e{parameters['iterations']}"
            f"-a{parameters['alpha']:g}-r{parameters['regularization']:g}-s{seed}"
        )
    if model == "bpr":
        return (
            f"e{parameters['iterations']}-f{parameters['factors']}"
            f"-lr{parameters['learning_rate']:g}-r{parameters['regularization']:g}-s{seed}"
        )
    if model == "bm25":
        return f"n{parameters['neighbors']}-k{parameters['k1']:g}-b{parameters['b']:g}"
    if model == "content":
        return f"idf{int(parameters['use_idf'])}-{parameters['aggregation']}"
    raise ValueError("parameter diagnostics support ease/als/bpr/bm25/content only")


def conditions(config):
    search = load_settings(config.path).get("search", {})
    cases = search.get("conditions")
    if config.protocol != "listening-v2" or config.grid or not isinstance(cases, list) or not cases:
        raise ValueError("parameter sensitivity requires listening-v2 and explicit conditions")
    allowed = {
        "ease": {"regularization"},
        "als": {"alpha", "regularization"},
        "bpr": {"learning_rate", "regularization"},
        "bm25": {"k1", "b", "neighbors"},
        "content": {"use_idf", "aggregation"},
    }.get(config.model, set())
    if not allowed or len(cases) * len(config.seeds) > config.max_trials:
        raise ValueError("unsupported model or conditions exceed max_trials")
    names = set()
    for case in cases:
        if not isinstance(case, dict) or not case or case.keys() - allowed:
            raise ValueError("unsupported sensitivity parameters")
        parameters = {**config.parameters, **case}
        for key, value in case.items():
            if key in {"use_idf", "aggregation"}:
                if (key == "use_idf" and type(value) is not bool) or (
                    key == "aggregation"
                    and (not isinstance(value, str) or value not in {"binary", "log_count"})
                ):
                    raise ValueError("invalid content parameter")
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("parameters must be finite numbers")
            if key == "b":
                valid = 0 <= value <= 1
            elif key == "alpha":
                valid = value >= 0
            else:
                valid = value > 0
            if not valid or (key == "neighbors" and type(value) is not int):
                raise ValueError("parameter outside supported range")
        for seed in config.seeds:
            name = condition_name(config.model, parameters, seed)
            if name in names:
                raise ValueError("duplicate sensitivity condition")
            names.add(name)
            yield (
                name,
                replace(
                    config,
                    name=f"{config.name}-{name}",
                    output=config.output / name,
                    parameters=parameters,
                    grid={},
                    seeds=(seed,),
                    stage="A",
                    max_trials=1,
                ),
            )


def controls(config):
    for entry in load_settings(config.path).get("search", {}).get("controls", []):
        if set(entry) != {"output", "parameters", "seed"}:
            raise ValueError("controls require output, parameters and seed")
        output = (config.path.parent / entry["output"]).resolve()
        if output == config.output or output.is_relative_to(config.output):
            raise ValueError("controls must be existing results outside the new study")
        parameters = {**config.parameters, **entry["parameters"]}
        yield (
            "control-" + condition_name(config.model, parameters, entry["seed"]),
            replace(config, output=output, parameters=parameters, seeds=(entry["seed"],)),
        )


def verify(trial, *, historical=False, validated=None):
    from src.infrastructure.provenance import source_hash

    result = json.loads((trial.output / "result.json").read_text(encoding="utf-8"))
    saved = json.loads((trial.output / "config.json").read_text(encoding="utf-8"))
    if validated is None:
        manifest, _, population = validate_bundle(trial.validation_dataset)
    else:
        manifest, population = validated
    expected = json.loads(json.dumps(asdict(trial), default=str))
    settings = saved["settings"]
    keys = ("model", "parameters", "seeds", "evaluation", "validation_dataset", "features")
    if (
        result.get("status") != "succeeded"
        or result.get("phase") != "validation"
        or manifest["phase"] != "validation"
        or any(settings[key] != expected[key] for key in keys)
        or saved["manifest"] != manifest
        or saved["population"] != population
        or result["population"] != population
        or result["model"] != trial.model
        or result["model_parameters"] != trial.parameters
        or result["primary"] != expected["evaluation"]
        or saved["features_sha256"] != file_sha256(trial.features)
    ):
        raise ValueError("completed condition input/parameters/population changed")
    for key in ("source_sha256", "dependency_lock_sha256"):
        value = saved.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("completed condition provenance hash is invalid")
    lock_hash = file_sha256(PROJECT_ROOT / "uv.lock")
    if not historical and saved["dependency_lock_sha256"] != lock_hash:
        raise ValueError("completed condition dependency lock changed")
    if not historical and (saved["source_sha256"] != source_hash() or settings != expected):
        raise ValueError("completed condition source/settings changed")
    for artifact in result["artifacts"].values():
        path = (trial.output / artifact["path"]).resolve()
        if (
            not path.is_relative_to(trial.output.resolve())
            or file_sha256(path) != artifact["sha256"]
        ):
            raise ValueError("completed condition artifact changed")
    return result, dict(
        output=str(trial.output),
        result_sha256=file_sha256(trial.output / "result.json"),
        config_sha256=file_sha256(trial.output / "config.json"),
        source_sha256=saved["source_sha256"],
        dependency_lock_sha256=saved["dependency_lock_sha256"],
        processing_source_changed=saved["source_sha256"] != source_hash(),
        dependency_lock_changed=saved["dependency_lock_sha256"] != lock_hash,
        execution=saved.get("execution"),
        historical=historical,
    )


def summarize(config, finished, output):
    if load_settings(config.path).get("search", {}).get("publication", False):
        from src.interfaces.notebook.parameter_figures import publish

        return publish(config, finished, output)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.interfaces.cli.comparison import metric_rows
    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    rows = []
    for name, trial, result, source in finished:
        label = condition_name(config.model, trial.parameters, trial.seeds[0])
        if config.model in {"als", "bpr"}:
            label = label.rsplit("-s", 1)[0]
        metadata = dict(
            condition=name, parameters=label, seed=trial.seeds[0], control=source["historical"]
        )
        # Save every period/day-weight, while plotting the shared primary display period.
        rows.extend(dict(**metadata, **row) for row in result["accuracy"])
        rows.extend(
            dict(**metadata, **row)
            for row in metric_rows(result, config.evaluation)
            if row["task"] in {"new", "repeat", "all"} and "window_days" not in row
        )
    frame = pd.DataFrame(rows)
    order = [
        "condition",
        "parameters",
        "control",
        "task",
        "window_days",
        "day_weight",
        "metric",
        "k",
    ]
    frame = frame[[*order, "mean", *[key for key in frame if key not in {*order, "mean"}]]]
    frame.to_csv(output / "metrics.csv", index=False)
    figures = []
    for (task, metric), subset in frame.groupby(["task", "metric"], sort=False):
        subset = subset.dropna(subset=["mean"])
        if subset.empty:
            continue
        subset = subset[
            subset.window_days.isna()
            | (
                (subset.window_days == config.evaluation.primary_window_days)
                & (
                    subset.day_weight.isna()
                    | (subset.day_weight == config.evaluation.primary_day_weight)
                )
            )
        ]
        if subset.empty:
            continue
        name, title = ACCURACY_NAMES.get((task, metric), (f"{metric}_{task}", metric))
        fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
        for label, group in subset.groupby("parameters", sort=False):
            if task == "all":
                ax.bar(label, group["mean"].mean())
            else:
                stats = group.groupby("k")["mean"].agg(["mean", "std"])
                ax.plot(stats.index, stats["mean"], label=label)
                if stats["std"].notna().any():
                    ax.fill_between(
                        stats.index,
                        stats["mean"] - stats["std"],
                        stats["mean"] + stats["std"],
                        alpha=0.12,
                    )
        ax.set(title=f"{title} · Validation {config.evaluation.primary_window_days}d", ylabel=title)
        if task == "all":
            ax.tick_params(axis="x", rotation=45, labelsize=8)
        else:
            ax.set(xlabel="K", xticks=range(2, 21, 2))
            ax.legend(fontsize=8)
        ax.grid(alpha=0.2)
        fig.supxlabel(
            "Mean ± seed SD where available; historical controls retain their source versions",
            fontsize=9,
        )
        fig.savefig(output / f"{name}.png", dpi=150)
        plt.close(fig)
        figures.append(f"{name}.png")
    return figures


def run_study(path, *, minimum_free_gb=5):
    from src.infrastructure.provenance import source_hash

    config = load_study_config(path)
    planned = list(conditions(config))
    references = list(controls(config))
    manifest, _, population = validate_bundle(config.validation_dataset)
    validated = manifest, population
    if manifest["phase"] != "validation":
        raise ValueError("Validation only; Test is not opened")
    identity = dict(
        format="music-recsys-parameter-sensitivity-config-v1",
        settings=json.loads(json.dumps(asdict(config), default=str)),
        search=load_settings(config.path)["search"],
        source_sha256=source_hash(),
        dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
        manifest_sha256=file_sha256(config.validation_dataset / "manifest.json"),
        features_sha256=file_sha256(config.features),
    )
    with study_lock(config), (config.output / "run.log").open("a") as log:
        saved = config.output / "config.json"
        if saved.exists() and json.loads(saved.read_text(encoding="utf-8")) != identity:
            raise ValueError("study source/input/plan changed; use a new output")
        if not saved.exists():
            atomic_json(saved, identity)
        marker = config.output / "result.json"
        if marker.exists():
            previous = json.loads(marker.read_text(encoding="utf-8"))
            for name, trial in [*planned, *references]:
                _, source = verify(
                    trial, historical=name.startswith("control-"), validated=validated
                )
                if previous["sources"][name] != source:
                    raise ValueError("published comparison source changed")
            return config.output
        finished = []
        if identity["search"].get("shared_neighbors"):
            if config.model != "bm25" or not config.parameters.get("use_gpu"):
                raise ValueError("shared_neighbors requires GPU BM25")
            groups = dict.fromkeys((t.parameters["k1"], t.parameters["b"]) for _, t in planned)
            for k1, b in groups:
                group = [
                    (n, t)
                    for n, t in planned
                    if t.parameters["k1"] == k1 and t.parameters["b"] == b
                ]
                if all((t.output / "result.json").exists() for _, t in group):
                    continue
                if any(t.output.exists() for _, t in group):
                    raise ValueError("partial shared-neighbor group; inspect preserved evidence")
                if shutil.disk_usage(config.output).free < minimum_free_gb * 1024**3:
                    raise RuntimeError("insufficient free disk")
                message = f"{datetime.now(UTC).isoformat()} START shared BM25 k1={k1:g} b={b:g}"
                print(message, flush=True)
                log.write(message + "\n")
                log.flush()
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "src.interfaces.cli.bm25_grid",
                        "--config",
                        str(config.path),
                        "--k1",
                        str(k1),
                        "--b",
                        str(b),
                    ],
                    check=True,
                )
        for name, trial in [*references, *planned]:
            historical = name.startswith("control-")
            if not historical and not (trial.output / "result.json").exists():
                if trial.output.exists() and any(p.name != ".lock" for p in trial.output.iterdir()):
                    raise ValueError(f"incomplete condition {name}; inspect before retrying")
                if shutil.disk_usage(config.output).free < minimum_free_gb * 1024**3:
                    raise RuntimeError("insufficient free disk")
                message = f"{datetime.now(UTC).isoformat()} START {name}"
                print(message, flush=True)
                log.write(message + "\n")
                log.flush()
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "src.interfaces.cli.sensitivity",
                        "--config",
                        str(config.path),
                        "--condition",
                        name,
                    ],
                    check=True,
                )
            result, source = verify(trial, historical=historical, validated=validated)
            comparable = {
                key: value
                for key, value in result["definitions"].items()
                if key not in {"ranking", "latency"}
            }
            if finished and comparable != {
                key: value
                for key, value in finished[0][2]["definitions"].items()
                if key not in {"ranking", "latency"}
            }:
                raise ValueError("comparison evaluation definitions differ")
            finished.append((name, trial, result, source))
        with staged_report(config.output) as temporary:
            figures = summarize(config, finished, temporary)
            atomic_json(
                temporary / "result.json",
                dict(
                    format="music-recsys-parameter-sensitivity-v1",
                    status="succeeded",
                    phase="validation",
                    planned=len(planned),
                    completed=len(planned),
                    figures=figures,
                    sources={name: source for name, _, _, source in finished},
                    note="No model selected; Test not opened; historical source differences retained",
                    source_sha256=identity["source_sha256"],
                    dependency_lock_sha256=identity["dependency_lock_sha256"],
                    inference=(
                        "Exploratory paired differences: each condition minus the first explicit baseline; user bootstrap 2000/seed42, CI at 90d/weight1/K10,20, means all windows/K; no multiplicity correction or training-seed replication; all metrics retained; no model selected"
                        if identity["search"].get("publication")
                        else None
                    ),
                    artifacts={
                        filename: dict(path=filename, sha256=file_sha256(temporary / filename))
                        for filename in ("metrics.csv", "paired.csv")
                        if (temporary / filename).exists()
                    },
                ),
            )
    return config.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--minimum-free-gb", type=float, default=5)
    parser.add_argument("--condition", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.minimum_free_gb) or args.minimum_free_gb < 0:
        parser.error("--minimum-free-gb must be finite and non-negative")
    if args.condition:
        from src.interfaces.cli.evaluate import run

        matches = [
            trial
            for name, trial in conditions(load_study_config(args.config))
            if name == args.condition
        ]
        if len(matches) != 1:
            parser.error("unknown condition")
        run(matches[0])
    else:
        print(run_study(args.config, minimum_free_gb=args.minimum_free_gb))


if __name__ == "__main__":
    main()
