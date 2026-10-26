"""Draw factor comparisons from prepared report rows; no result or file writes."""

from contextlib import contextmanager

from src.interfaces.notebook.plots import figure_scope


@contextmanager
def draw(frame, config):
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES, factor_figures

    with figure_scope():
        figures = {}
        for (task, metric), values in frame.groupby(["task", "metric"], sort=False):
            if not values["mean"].notna().any():
                continue
            name, title = ACCURACY_NAMES.get(
                (task, metric), (f"{metric}_{task}", f"{metric} · {task}")
            )
            for suffix, subset in (
                ("", values),
                ("_factors", values[values.model == config.model]),
                ("_k20", values[(values.model == config.model) & (values.k == 20)]),
            ):
                if not subset["mean"].notna().any():
                    continue
                fig, ax = plt.subplots(figsize=(16, 7))
                if task == "all" or suffix == "_k20":
                    ax.bar(range(len(subset)), subset["mean"])
                    ax.set_xticks(
                        range(len(subset)),
                        [
                            f"factors={int(f)}" if suffix == "_k20" else label
                            for f, label in zip(subset.factors, subset.experiment, strict=True)
                        ],
                    )
                    ax.tick_params(axis="x", labelrotation=15, labelsize=6)
                else:
                    for label, group in subset.groupby("experiment", sort=False):
                        group = group.sort_values("k")
                        ax.plot(group.k, group["mean"], marker="o", markersize=3, label=label)
                    ax.set(xlabel="K", xticks=sorted(subset.k.unique()))
                    ax.legend(fontsize=6, loc="upper left", bbox_to_anchor=(1.01, 1))
                ax.set(
                    ylabel=title,
                    title=f"{title}{' @20' if suffix == '_k20' else ''} · Validation {config.evaluation.primary_window_days}d",
                    ylim=(0, None),
                )
                ax.grid(alpha=0.2)
                fig.tight_layout()
                filename = f"{name}{suffix}.png"
                figures[filename] = fig
        for name, figure in factor_figures(
            frame, config.model, config.evaluation.primary_window_days
        ).items():
            figures[f"{name}.png"] = figure
        yield figures
