"""Draw sensitivity figures from prepared tables without reading or saving artifacts."""

from contextlib import contextmanager

from backend.interfaces.notebook.plots import figure_scope


@contextmanager
def draw(frame, config):
    with figure_scope():
        yield _figures(frame, config)


def _figures(frame, config):
    import matplotlib.pyplot as plt

    from backend.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

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
        yield f"{name}.png", fig
