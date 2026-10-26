"""Draw factor study summaries from prepared rows; no result or file writes."""

from contextlib import contextmanager

from src.interfaces.notebook.plots import figure_scope


@contextmanager
def draw(frame, config):
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    with figure_scope():
        figures = {}
        for (task, metric), subset in frame.groupby(["task", "metric"], sort=False):
            subset = subset.dropna(subset=["mean"])
            if subset.empty:
                continue
            name, title = ACCURACY_NAMES.get((task, metric), (f"{metric}_{task}", metric))
            fig, ax = plt.subplots(figsize=(12, 7), layout="constrained")
            for (model, factors, epochs, neighbors, alpha, regularization), group in subset.groupby(
                ["model", "factors", "epochs", "neighbors", "alpha", "regularization"],
                dropna=False,
                sort=False,
            ):
                label = (
                    f"BPR f={int(factors)}, epochs={int(epochs)}"
                    if model == "bpr"
                    else (
                        f"ALS f={int(factors)}, alpha={alpha:g}, reg={regularization:g}"
                        if model == "als"
                        else f"BM25 n={int(neighbors)}"
                    )
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
            ax.set(
                title=f"{title} · Validation {config.evaluation.primary_window_days}d", ylabel=title
            )
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
            figures[f"{name}.png"] = fig
        yield figures
