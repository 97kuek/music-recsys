"""Draw ItemCF curves and heatmaps from prepared rows; no result or file writes."""

from contextlib import contextmanager

from src.interfaces.notebook.plots import figure_scope


def comparison_curves(frame, figures):
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    for task, metric in (
        ("discovery", "recall"),
        ("repeat", "repeat_recall"),
        ("discovery", "binary_ndcg"),
    ):
        values = frame[(frame.task == task) & (frame.metric == metric)]
        if values.empty:
            raise ValueError(f"Missing comparison metric: {task}/{metric}")
        if not values["mean"].notna().any():
            continue
        name, title = ACCURACY_NAMES[task, metric]
        fig, ax = plt.subplots(figsize=(8, 4.8))
        for (similarity, neighbors), group in values.groupby(["similarity", "neighbors"]):
            group = group.sort_values("k")
            style = {20: ("-", "o"), 50: ("--", "s"), 100: (":", "^")}
            line, marker = style.get(neighbors, ("-", "o"))
            ax.plot(
                group.k,
                group["mean"],
                color={"cosine": "#0072B2", "jaccard": "#D55E00"}[similarity],
                linestyle=line,
                marker=marker,
                markersize=4,
                label=f"{similarity.capitalize()} / {neighbors}",
            )
        ax.set(xlabel="K", ylabel=title, title=f"{title}@K", xticks=sorted(values.k.unique()))
        ax.set_ylim(0, max(float(values["mean"].max()) * 1.1, 0.001))
        ax.grid(alpha=0.2)
        ax.legend(title="Similarity / Neighbors", fontsize=9)
        fig.tight_layout()
        filename = f"{name}.png"
        figures[filename] = fig

        if not values.loc[values.k == 20, "mean"].notna().any():
            continue
        fig, ax = plt.subplots(figsize=(6, 4))
        for similarity, group in values[values.k == 20].groupby("similarity"):
            group = group.sort_values("neighbors")
            ax.plot(
                group.neighbors,
                group["mean"],
                marker="o",
                label=similarity.capitalize(),
                color={"cosine": "#0072B2", "jaccard": "#D55E00"}[similarity],
            )
        ax.set(
            xlabel="Neighbors",
            ylabel=title,
            title=f"{title}@20",
            xticks=sorted(values.neighbors.unique()),
        )
        ax.set_ylim(0, max(float(values.loc[values.k == 20, "mean"].max()) * 1.1, 0.001))
        ax.grid(alpha=0.2)
        ax.legend()
        fig.tight_layout()
        filename = f"{name}_neighbors.png"
        figures[filename] = fig


@contextmanager
def draw(frame, config):
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    with figure_scope():
        figures = {}
        comparison_curves(frame, figures)
        selected = frame[(frame.k == 20) | frame.k.isna()]
        for (task, metric), values in selected.groupby(["task", "metric"], sort=False):
            if not values["mean"].notna().any():
                continue
            table = values.pivot(index="similarity", columns="neighbors", values="mean")
            name, title = ACCURACY_NAMES.get(
                (task, metric), (f"{metric}_{task}", f"{metric} · {task}")
            )
            fig, ax = plt.subplots(figsize=(5.5, 3))
            picture = ax.imshow(table.to_numpy(float), aspect="auto", cmap="viridis")
            ax.set(
                xticks=range(len(table.columns)),
                xticklabels=table.columns,
                yticks=range(len(table.index)),
                yticklabels=table.index,
                xlabel="Neighbors",
                ylabel="Similarity",
                title=f"{title} @20" if task != "all" else title,
            )
            for i in range(len(table.index)):
                for j in range(len(table.columns)):
                    value = table.iloc[i, j]
                    if pd.notna(value):
                        ax.text(
                            j,
                            i,
                            f"{value:.4f}",
                            ha="center",
                            va="center",
                            color="white",
                            bbox=dict(facecolor="black", alpha=0.4, edgecolor="none", pad=2),
                        )
            fig.colorbar(picture, ax=ax)
            fig.tight_layout()
            filename = f"{name}_heatmap.png"
            figures[filename] = fig
        yield figures
