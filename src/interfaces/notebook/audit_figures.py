"""Common-population and stratified plots for explicitly listed saved recommenders."""

from src.interfaces.notebook.parameter_figures import save
from src.interfaces.notebook.plots import axes, finish_k, plot_k


def render(output, rows):
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame(rows)
    baseline = next(r["model_left"] for r in rows if r["model_left"] is not None)
    content = [name for name in frame.model_left.dropna().unique() if name.startswith("content-")]
    chosen = [baseline, *content]
    figures = []
    overall = frame[frame.stratum_kind == "all"]
    for task in ("new", "repeat"):
        for metric in ("common_ild", "jaccard", "union_recall", "unique_hits_right"):
            selected = overall[(overall.task == task) & (overall.metric == metric)]
            selected = selected[selected.window_days.isna() | (selected.window_days == 90)]
            if metric == "common_ild":
                selected = selected[selected.model_left.isin(chosen)]
            else:
                selected = selected[
                    (selected.model_left == baseline) & selected.model_right.isin(content)
                ]
            if not selected["mean"].notna().any():
                continue
            fig, ax = axes(figsize=(8.0, 4.5))
            key = "model_left" if metric == "common_ild" else "model_right"
            for i, (name, group) in enumerate(selected.groupby(key, sort=False)):
                plot_k(
                    ax,
                    group,
                    "mean",
                    index=i,
                    label=name,
                    scale=100 if metric == "union_recall" else 1,
                )
            ylabel = {
                "common_ild": "ILD on common valid users",
                "jaccard": "Recommendation Jaccard overlap",
                "union_recall": "Union recall (%) — oracle diagnostic",
                "unique_hits_right": "Hits retrieved only by Content",
            }[metric]
            finish_k(ax, sorted(selected.k.unique()), ylabel)
            figures.append(save(fig, output, f"{metric}_{task}"))
        for kind in ("history", "popularity"):
            selected = frame[
                (frame.task == task)
                & (frame.metric == "recall")
                & (frame.k == 20)
                & (frame.window_days == 90)
                & (frame.stratum_kind == kind)
                & frame.model_left.isin(chosen)
                & frame.model_right.isna()
            ]
            if not selected["mean"].notna().any():
                continue
            fig, ax = axes(figsize=(8.0, 4.5))
            for i, name in enumerate(chosen):
                group = (
                    selected[selected.model_left == name]
                    .set_index("stratum")
                    .reindex(["Q1", "Q2", "Q3", "Q4"])
                )
                ax.scatter(
                    np.arange(4) + (i - (len(chosen) - 1) / 2) * 0.12,
                    group["mean"] * 100,
                    label=name,
                    s=25,
                )
            ax.set(
                xticks=range(4),
                xticklabels=["Q1", "Q2", "Q3", "Q4"],
                xlabel="Training history band"
                if kind == "history"
                else "Training item-popularity band",
                ylabel="New-track recall@20 (%)" if task == "new" else "Repeat recall@20 (%)",
                ylim=(0, None),
            )
            ax.legend(frameon=False, fontsize=8)
            figures.append(save(fig, output, f"{kind}_{task}"))
    return figures
