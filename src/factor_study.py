"""Sequential, resumable BPR epoch/factor/seed experiments using full Validation."""

import argparse
import itertools
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from src.experiment_search import load_study_config, study_lock
from src.experiment_store import atomic_json
from src.full_evaluation import run
from src.listening_data import file_sha256


def conditions(config):
    from src.recommenders.factors import integer

    if config.model != "bpr" or set(config.grid) != {"factors", "iterations"}:
        raise ValueError("BPR study requires factors/iterations grid")
    count = len(config.seeds)
    for values in config.grid.values():
        count *= len(values)
        for value in values:
            integer(value, "grid value")
    if count > config.max_trials:
        raise ValueError("BPR study exceeds max_trials")
    for factors, epochs, seed in itertools.product(
        config.grid["factors"], config.grid["iterations"], config.seeds
    ):
        name = f"f{factors}-e{epochs}-s{seed}"
        yield (
            name,
            replace(
                config,
                name=f"{config.name}-{name}",
                output=config.output / name,
                parameters={**config.parameters, "factors": factors, "iterations": epochs},
                grid={},
                seeds=(seed,),
                stage="A",
                max_trials=1,
            ),
        )


def verify(trial):
    from src.comparison import verified_result
    from src.evaluation.benchmark import _source_sha256
    from src.model_artifacts import load_model

    result, saved, _, _ = verified_result(trial, trial.evaluation)
    if saved["source_sha256"] != _source_sha256():
        raise ValueError("completed condition source changed; use a new study output")
    if saved["dependency_lock_sha256"] != file_sha256(
        Path(__file__).resolve().parents[1] / "uv.lock"
    ):
        raise ValueError("completed condition dependency lock changed")
    serving = result["serving_model"]
    load_model(trial.output / "model", expected_manifest_sha256=serving["manifest_sha256"])
    return result


def summarize(config, finished):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.comparison import metric_rows
    from src.evaluation_figures import ACCURACY_NAMES

    rows = []
    for name, trial, result in finished:
        rows.extend(
            dict(
                condition=name,
                model=trial.model,
                seed=trial.seeds[0],
                factors=trial.parameters.get("factors"),
                epochs=trial.parameters.get("iterations"),
                neighbors=trial.parameters.get("neighbors"),
                **row,
            )
            for row in metric_rows(result, config.evaluation)
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(config.output / "metrics.csv", index=False)
    figures = []
    for (task, metric), subset in frame.groupby(["task", "metric"], sort=False):
        subset = subset.dropna(subset=["mean"])
        if subset.empty:
            continue
        name, title = ACCURACY_NAMES.get((task, metric), (f"{metric}_{task}", metric))
        fig, ax = plt.subplots(figsize=(12, 7), layout="constrained")
        for (model, factors, epochs, neighbors), group in subset.groupby(
            ["model", "factors", "epochs", "neighbors"], dropna=False, sort=False
        ):
            label = (
                f"BPR f={int(factors)}, epochs={int(epochs)}"
                if model == "bpr"
                else f"BM25 n={int(neighbors)}"
            )
            if task == "all":
                ax.bar(
                    label,
                    group["mean"].mean(),
                    yerr=group["mean"].std(ddof=1) if len(group) > 1 else None,
                )
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
            ax.tick_params(axis="x", rotation=50, labelsize=8)
        else:
            ax.set(xlabel="K", xticks=range(2, 21, 2))
            ax.legend(fontsize=8)
        ax.grid(alpha=0.2)
        fig.supxlabel(
            "Independent epoch runs; mean ± seed SD; GPU parallel updates are not bitwise reproducible",
            fontsize=9,
        )
        path = config.output / f"{name}.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        figures.append(path.name)
    return figures


def run_study(path, *, minimum_free_gb=5):
    from src.evaluation.benchmark import _source_sha256

    config = load_study_config(path)
    planned = list(conditions(config))
    identity = dict(
        format="music-recsys-factor-study-config-v1",
        settings=json.loads(json.dumps(asdict(config), default=str)),
        source_sha256=_source_sha256(),
        dependency_lock_sha256=file_sha256(Path(__file__).resolve().parents[1] / "uv.lock"),
        manifest_sha256=file_sha256(config.validation_dataset / "manifest.json"),
        features_sha256=file_sha256(config.features),
        plan=[
            dict(condition=name, parameters=trial.parameters, seed=trial.seeds[0])
            for name, trial in planned
        ],
    )
    with study_lock(config), (config.output / "run.log").open("a") as log:
        saved = config.output / "config.json"
        if saved.exists() and json.loads(saved.read_text()) != identity:
            raise ValueError("study source/input/plan changed; use a new output")
        if not saved.exists():
            atomic_json(saved, identity)
        previous = config.output / "result.json"
        if previous.exists() and json.loads(previous.read_text()).get("status") == "succeeded":
            for _, trial in planned:
                verify(trial)
            return config.output
        state = dict(
            format="music-recsys-factor-study-v1",
            status="running",
            phase="validation",
            planned=len(planned),
            completed=0,
            conditions=[],
            figures=[],
        )
        finished = []

        def record(message):
            text = f"{datetime.now(UTC).isoformat()} {message}"
            print(text, flush=True)
            log.write(text + "\n")
            log.flush()

        try:
            for name, trial in planned:
                if shutil.disk_usage(config.output).free < minimum_free_gb * 1024**3:
                    raise RuntimeError(
                        f"less than {minimum_free_gb} GiB free disk; resume after freeing space"
                    )
                state["current"] = name
                atomic_json(config.output / "result.json", state)
                if not (trial.output / "result.json").exists():
                    if (trial.output / "model").exists():
                        raise ValueError(
                            f"incomplete condition {name} has a model; inspect before resuming"
                        )
                    record(f"START {name} ({state['completed'] + 1}/{len(planned)})")
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "src.factor_study",
                            "--config",
                            str(config.path),
                            "--condition",
                            name,
                        ],
                        check=True,
                    )
                result = verify(trial)
                finished.append((name, trial, result))
                state["conditions"].append(
                    dict(
                        condition=name,
                        status="succeeded",
                        result=f"{name}/result.json",
                        result_sha256=file_sha256(trial.output / "result.json"),
                        training_seconds=result["efficiency"]["training_seconds"],
                        evaluation_seconds=result["efficiency"]["evaluation_seconds"],
                    )
                )
                state["completed"] = len(finished)
                record(f"DONE {name}; completed={len(finished)}/{len(planned)}")
                atomic_json(config.output / "result.json", state)
            state["figures"] = summarize(config, finished)
            state.update(status="succeeded", current=None)
            record("SUCCEEDED: all conditions completed; no model selected; Test not opened")
        except Exception as error:
            state.update(status="failed", error=f"{type(error).__name__}: {error}")
            record(state["error"])
            raise
        finally:
            atomic_json(config.output / "result.json", state)
    return config.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--condition", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.condition:
        config = load_study_config(args.config)
        matches = [trial for name, trial in conditions(config) if name == args.condition]
        if len(matches) != 1:
            parser.error("unknown condition")
        trial = matches[0]
        run(
            trial,
            export_path=trial.output / "model",
            model_version=f"{config.name}-{args.condition}",
        )
    else:
        started = time.perf_counter()
        print(run_study(args.config), f"elapsed={time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
