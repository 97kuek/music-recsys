"""Pair-specific Hybrid comparisons: one metric per figure and all-null costs omitted."""

from src.interfaces.notebook.parameter_figures import ACCURACY, DIVERSITY, save
from src.interfaces.notebook.plots import STYLES, axes, finish_k, metric_label, plot_k


def figures(output, rows, common):
    import matplotlib

    matplotlib.use("Agg")
    import pandas as pd

    frame = pd.DataFrame(rows)
    shared = pd.DataFrame(common)
    names = []
    for a, b in (("ease", "bpr"), ("bm25", "content")):
        prefix = a + "-" + b
        groups = [a, b, prefix + "-rrf", prefix + "-mmr0.8", prefix + "-mmr0.5"]
        for task, metric, name in (*ACCURACY, *DIVERSITY):
            values = (
                shared[(shared.pair == prefix) & (shared.task == task)].assign(metric="ild")
                if metric == "ild"
                else frame[
                    (frame.task == task) & (frame.metric == metric) & (frame.condition.isin(groups))
                ]
            )
            if "window_days" in values:
                values = values[
                    values.window_days.isna()
                    | (
                        (values.window_days == 90)
                        & (values.day_weight.isna() | (values.day_weight == 1))
                    )
                ]
            if values.empty or not values["mean"].notna().any():
                continue
            label, scale = metric_label(metric, task)
            fig, ax = axes(figsize=(8, 4.5))
            for i, group in enumerate(groups):
                sub = values[values.condition == group]
                if not sub.empty:
                    plot_k(ax, sub, "mean", index=i, label=group, scale=scale, interval=False)
            finish_k(ax, sorted(values.k.unique()), label)
            names.append(save(fig, output, f"{name}_{prefix}"))
            point = values[values.k == 20]
            if point.empty:
                continue
            fig, ax = axes(figsize=(8, 4.3))
            for i, group in enumerate(groups):
                sub = point[point.condition == group]
                if sub.empty:
                    continue
                row = sub.iloc[0]
                error = None
                if pd.notna(row.get("ci_low")) and pd.notna(row.get("ci_high")):
                    error = [
                        [max(0, row["mean"] - row.ci_low) * scale],
                        [max(0, row.ci_high - row["mean"]) * scale],
                    ]
                ax.errorbar(
                    row["mean"] * scale,
                    i,
                    xerr=error,
                    fmt="o",
                    color=STYLES[i % len(STYLES)]["color"],
                    capsize=3,
                )
            ax.set(
                xlabel=label.replace("$K$", "20"),
                yticks=range(5),
                yticklabels=groups,
                xlim=(0, None),
            )
            ax.invert_yaxis()
            names.append(save(fig, output, f"{name}_{prefix}_k20"))
        for metric, unit in (
            ("training_seconds", "s"),
            ("ranking_seconds", "s"),
            ("latency_mean", "ms"),
            ("evaluation_seconds", "s"),
        ):
            values = frame[
                (frame.aspect == "efficiency")
                & (frame.metric == metric)
                & frame.condition.isin(groups)
            ].dropna(subset=["mean"])
            if values.empty:
                continue
            fig, ax = axes(figsize=(8, 4.2))
            values = values.set_index("condition").reindex(groups)
            ax.barh(
                range(5), values["mean"] * (1000 if unit == "ms" else 1), color=STYLES[0]["color"]
            )
            ax.set(
                yticks=range(5),
                yticklabels=groups,
                xlabel=metric.replace("_", " ") + f" ({unit})",
                xlim=(0, None),
            )
            ax.invert_yaxis()
            names.append(save(fig, output, f"{metric}_{prefix}"))
    return names
