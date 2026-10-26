"""Sensitivity tables and figures for ordinary and publication reports."""

from src.infrastructure.research.configuration import load_settings
from src.infrastructure.research.parameters import condition_name


def summarize(config, finished, output):
    if load_settings(config.path).get("search", {}).get("publication", False):
        from src.interfaces.notebook.parameter_figures import publish

        return publish(config, finished, output)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.infrastructure.research.comparison import metric_rows
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
