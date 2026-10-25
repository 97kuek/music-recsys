"""Verified, read-only BPR diagnostics from completed Validation experiments."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.comparison import staged_report, verified_result
from src.interfaces.cli.factor_study import conditions


def load_points(config, k):
    import pandas as pd

    if config.model != "bpr" or len(config.grid.get("iterations", [])) != 1:
        raise ValueError("BPR figures require a single-epoch study")
    if k not in config.evaluation.ks:
        raise ValueError("K is outside the saved evaluation")
    root = config.output
    saved = json.loads((root / "config.json").read_text())
    state = json.loads((root / "result.json").read_text())
    if saved["settings"] != json.loads(json.dumps(asdict(config), default=str)):
        raise ValueError("Saved study settings differ")
    planned = dict(conditions(config))
    entries = {entry["condition"]: entry for entry in state["conditions"]}
    if (
        state["status"] != "succeeded"
        or state["phase"] != "validation"
        or state["planned"] != state["completed"]
        or len(entries) != len(state["conditions"])
        or len(entries) != state["completed"]
        or entries.keys() != planned.keys()
    ):
        raise ValueError("Complete Validation conditions are required")
    if file_sha256(config.validation_dataset / "manifest.json") != saved["manifest_sha256"]:
        raise ValueError("Validation manifest changed")
    rows, sources, reference = [], [], None
    for name, trial in planned.items():
        path = trial.output / "result.json"
        if (
            entries[name]["status"] != "succeeded"
            or file_sha256(path) != entries[name]["result_sha256"]
        ):
            raise ValueError("Condition result changed")
        result, child, identity, source = verified_result(trial, config.evaluation)
        if reference is None:
            reference = identity
        if identity != reference or any(
            child[key] != saved[key] for key in ("source_sha256", "dependency_lock_sha256")
        ):
            raise ValueError("Condition input or implementation differs")
        row = dict(
            condition=name,
            factors=trial.parameters["factors"],
            seed=trial.seeds[0],
            training_seconds=result["efficiency"]["training_seconds"],
        )
        for metric in ("binary_ndcg", "recall"):
            scores = [
                score
                for score in result["accuracy"]
                if score["task"] == "discovery"
                and score["metric"] == metric
                and score["window_days"] == config.evaluation.primary_window_days
                and score["k"] == k
                and score["day_weight"] is None
            ]
            if len(scores) != 1:
                raise ValueError("Missing or duplicated Validation metric")
            row[metric] = scores[0]["mean"]
        rows.append(row)
        sources.append(dict(**source, config_sha256=file_sha256(trial.output / "config.json")))
    frame = pd.DataFrame(rows).sort_values(["factors", "seed"])
    return frame, sources


def figures(frame, *, epochs, window_days, k):
    import matplotlib.pyplot as plt
    import numpy as np

    required = ["factors", "seed", "training_seconds", "binary_ndcg", "recall"]
    if frame.empty or not np.isfinite(frame[required].to_numpy(float)).all():
        raise ValueError("Figures require measured, finite values")
    if frame.duplicated(["factors", "seed"]).any():
        raise ValueError("Duplicated factor/seed condition")
    seeds = sorted(frame.seed.unique())
    if len(seeds) < 2 or any(set(g.seed) != set(seeds) for _, g in frame.groupby("factors")):
        raise ValueError("Each factor count requires the same multiple seeds")
    stats = frame.groupby("factors")[["training_seconds", "binary_ndcg", "recall"]].agg(
        ["mean", "std"]
    )
    result = {}
    colors = ["#0072B2", "#D55E00", "#009E73", "#CC79A7"]
    caption = f"{len(seeds)} seeds · mean ± sample SD (ddof=1), not a confidence interval"

    def canvas(title, xlabel, ylabel, footer=caption):
        fig, ax = plt.subplots(figsize=(9, 6.5))
        fig.subplots_adjust(left=0.14, right=0.96, top=0.79, bottom=0.19)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        fig.suptitle(title, x=0.14, y=0.96, ha="left", fontsize=20, fontweight="bold")
        fig.text(
            0.14,
            0.87,
            f"BPR · {epochs} epochs · Validation {window_days} days · New tracks · K={k}",
            fontsize=11,
            color="#526174",
        )
        ax.set(xlabel=xlabel, ylabel=ylabel)
        ax.tick_params(labelsize=11)
        ax.xaxis.label.set_size(12)
        ax.yaxis.label.set_size(12)
        ax.grid(alpha=0.18)
        ax.spines[["top", "right"]].set_visible(False)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        fig.text(0.14, 0.07, footer, fontsize=9, color="#526174")
        return fig, ax

    for metric, filename, label in (
        ("binary_ndcg", "ndcg_factors.png", f"Validation Binary NDCG@{k}"),
        ("recall", "recall_factors.png", f"Validation Recall@{k}"),
    ):
        fig, ax = canvas(f"Factors vs {label}", "Factors", label)
        ax.errorbar(
            stats.index,
            stats[metric]["mean"],
            yerr=stats[metric]["std"],
            fmt="o-",
            capsize=6,
            linewidth=2,
            markersize=7,
            color=colors[0],
            label=f"Mean ± SD across {len(seeds)} seeds",
        )
        ax.set_xticks(stats.index)
        ax.margins(x=0.08, y=0.18)
        ax.legend(frameon=False)
        result[filename] = fig

    fig, ax = canvas(
        "Seed variation in recommendation accuracy",
        "Factors",
        f"Validation Binary NDCG@{k}",
        footer="Each line is one seed · identical Validation split and candidate rules",
    )
    for i, (seed, group) in enumerate(frame.groupby("seed", sort=True)):
        group = group.sort_values("factors")
        ax.plot(
            group.factors,
            group.binary_ndcg,
            label=f"Seed {seed}",
            color=colors[i % len(colors)],
            marker=["o", "s", "^"][i % 3],
            linestyle=["-", "--", "-."][i % 3],
            linewidth=2,
            markersize=7,
        )
    ax.set_xticks(stats.index)
    ax.margins(x=0.08, y=0.18)
    ax.legend(frameon=False)
    result["ndcg_seeds.png"] = fig

    fig, ax = canvas(
        "Training time vs recommendation accuracy",
        "Training time (seconds)",
        f"Validation Binary NDCG@{k}",
        footer=caption
        + "\nRecorded training time includes data/setup/fit/transfer; evaluation excluded",
    )
    for i, (factors, group) in enumerate(frame.groupby("factors", sort=True)):
        color = colors[i % len(colors)]
        ax.scatter(group.training_seconds, group.binary_ndcg, color=color, alpha=0.25, s=30)
        ax.errorbar(
            stats.loc[factors, ("training_seconds", "mean")],
            stats.loc[factors, ("binary_ndcg", "mean")],
            xerr=stats.loc[factors, ("training_seconds", "std")],
            yerr=stats.loc[factors, ("binary_ndcg", "std")],
            fmt="o",
            capsize=5,
            markersize=8,
            color=color,
            label=f"{factors} factors",
        )
    ax.margins(x=0.12, y=0.18)
    ax.legend(frameon=False)
    result["time_ndcg.png"] = fig
    return result


def render(config_path, *, k=20):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from src.infrastructure.provenance import source_hash

    config = load_study_config(config_path)
    with study_lock(config):
        frame, sources = load_points(config, k)
        images = figures(
            frame,
            epochs=config.grid["iterations"][0],
            window_days=config.evaluation.primary_window_days,
            k=k,
        )
        try:
            if any((config.output / name).exists() for name in images):
                raise FileExistsError("Diagnostic figures already exist; saved files are preserved")
            document = json.loads((config.output / "result.json").read_text())
            with staged_report(config.output) as stage:
                for name, figure in images.items():
                    figure.savefig(stage / name, dpi=180, facecolor="white")
                document["figures"] = list(dict.fromkeys([*document["figures"], *images]))
                document["bpr_figures"] = dict(
                    window_days=config.evaluation.primary_window_days,
                    k=k,
                    task="discovery",
                    aggregation="mean and sample standard deviation across seeds; ddof=1",
                    sources=sources,
                    render_source_sha256=source_hash(),
                    figures={name: file_sha256(stage / name) for name in images},
                )
                atomic_json(stage / "result.json", document)
        finally:
            for figure in images.values():
                plt.close(figure)
    return [config.output / name for name in images]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--k", type=int, default=20)
    args = parser.parse_args()
    for path in render(args.config, k=args.k):
        print(path)


if __name__ == "__main__":
    main()
