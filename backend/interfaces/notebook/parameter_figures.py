"""Publication figures for explicit Content and BM25 Validation experiments."""

from contextlib import contextmanager

from backend.interfaces.notebook.plots import axes, figure_scope, finish_k, metric_label, plot_k

ACCURACY = (
    ("discovery", "binary_ndcg", "binary_ndcg"),
    ("discovery", "recall", "recall"),
    ("retention_proxy", "graded_ndcg", "graded_new"),
    ("repeat", "graded_ndcg", "graded_repeat"),
    ("repeat", "repeat_recall", "repeat_recall"),
)
DIVERSITY = tuple(
    (task, metric, f"{metric}_{task}")
    for task in ("new", "repeat")
    for metric in ("ild", "catalog_coverage", "novelty")
)
EFFICIENCY = (
    ("all", "training_seconds", "training"),
    ("all", "ranking_seconds", "ranking"),
    ("all", "evaluation_seconds", "evaluation"),
    ("all", "latency_mean", "latency"),
    ("all", "generation_calls", "calls"),
)


def primary(frame, config):
    return frame[
        frame.window_days.isna()
        | (
            (frame.window_days == config.evaluation.primary_window_days)
            & (frame.day_weight.isna() | (frame.day_weight == config.evaluation.primary_day_weight))
        )
    ]


def comparison_curves(frame, shared, conditions):
    """Saved comparisons at 90 days/weight 1, using the common-user ILD rows."""
    for task, metric, name in (*ACCURACY, *DIVERSITY):
        values = (
            shared[shared.task == task]
            if metric == "ild"
            else frame[(frame.task == task) & (frame.metric == metric)]
        )
        if "window_days" in values:
            values = values[
                values.window_days.isna()
                | (values.window_days.eq(90) & values.day_weight.fillna(1).eq(1))
            ]
        if values.empty or not values["mean"].notna().any():
            continue
        label, scale = metric_label(metric, task)
        fig, ax = axes(figsize=(8, 4.5))
        for index, condition in enumerate(conditions):
            group = values[values.condition == condition]
            plot_k(ax, group, "mean", index=index, label=condition, scale=scale, interval=False)
        finish_k(ax, sorted(values.k.unique()), label)
        yield name, fig


def draw_grid(frame):
    import numpy as np

    for task, metric, name in (*ACCURACY, *DIVERSITY, *EFFICIENCY):
        values = frame[(frame.task == task) & (frame.metric == metric)]
        values = values[values.k.isna() | (values.k == 20)]
        if values["mean"].notna().sum() == 0:
            continue
        label, scale = (
            metric_label(metric, task)
            if task != "all"
            else (
                metric.replace("_", " ")
                + (" (s)" if "seconds" in metric or "latency" in metric else ""),
                1,
            )
        )
        for n, group in values.groupby("neighbors"):
            if not group["mean"].notna().any():
                continue
            if group.duplicated(["k1", "b"]).any():
                raise ValueError("Duplicate BM25 grid cell")
            grid = (
                group.pivot(index="k1", columns="b", values="mean").sort_index().sort_index(axis=1)
            )
            fig, ax = axes(figsize=(6.5, 3.5))
            ax.grid(False)
            data = grid.to_numpy(dtype=float) * scale
            artist = ax.imshow(
                np.ma.masked_invalid(data), interpolation="nearest", aspect="auto", cmap="cividis"
            )
            ax.set(
                xticks=range(len(grid.columns)),
                xticklabels=[f"{v:g}" for v in grid.columns],
                yticks=range(len(grid.index)),
                yticklabels=[f"{v:g}" for v in grid.index],
                xlabel=r"Length normalization, $b$ (tested levels)",
                ylabel=r"Saturation, $k_1$ (tested levels)",
            )
            for row, column in np.ndindex(data.shape):
                value = data[row, column]
                ax.text(
                    column,
                    row,
                    f"{value:.3g}" if np.isfinite(value) else "—",
                    ha="center",
                    va="center",
                    color="white" if np.isfinite(value) and artist.norm(value) < 0.4 else "black",
                    fontsize=9,
                )
            fig.colorbar(artist, ax=ax, label=label.replace("$K$", "20"))
            yield f"{name}_n{int(n)}.png", fig


def draw_curves(frame, config):
    selected = frame
    if config.model == "bm25":
        selected = frame[(frame.k1 == 1.2) & (frame.b == 0.75)]
    for task, metric, name in (*ACCURACY, *DIVERSITY):
        values = selected[(selected.task == task) & (selected.metric == metric)]
        if not values["mean"].notna().any():
            continue
        fig, ax = axes()
        label, scale = metric_label(metric, task)
        for i, (condition, group) in enumerate(values.groupby("condition", sort=False)):
            description = (
                f"n = {int(group.neighbors.iloc[0])}"
                if config.model == "bm25"
                else f"IDF {'on' if group.use_idf.iloc[0] else 'off'}, {group.aggregation.iloc[0]}"
            )
            plot_k(ax, group, "mean", index=i, label=description, scale=scale)
        finish_k(ax, config.evaluation.ks, label)
        yield f"{name}_k.png", fig


def draw_content_points(frame):
    for task, metric, name in (*ACCURACY, *DIVERSITY, *EFFICIENCY):
        selected = frame[(frame.task == task) & (frame.metric == metric)]
        selected = selected[selected.k.isna() | (selected.k == 20)].dropna(subset=["mean"])
        if selected.empty:
            continue
        label, scale = (
            metric_label(metric, task)
            if task != "all"
            else (
                metric.replace("_", " ")
                + (" (s)" if "seconds" in metric or "latency" in metric else ""),
                1,
            )
        )
        fig, ax = axes(figsize=(7.4, 3.5))
        for position, row in enumerate(selected.itertuples()):
            estimate = row.mean * scale
            ax.plot(estimate, position, "o", color="#0072B2")
            if row.ci_low == row.ci_low and row.ci_high == row.ci_high:
                ax.errorbar(
                    estimate,
                    position,
                    xerr=[[(row.mean - row.ci_low) * scale], [(row.ci_high - row.mean) * scale]],
                    fmt="none",
                    color="#0072B2",
                    capsize=3,
                )
        ax.set(
            yticks=range(len(selected)),
            yticklabels=[
                f"IDF {'on' if r.use_idf else 'off'}, {r.aggregation}"
                for r in selected.itertuples()
            ],
            xlabel=label.replace("$K$", "20"),
        )
        ax.invert_yaxis()
        ax.set_xlim(left=0)
        yield name + ".png", fig


def draw_tradeoffs(frame):
    for task, metric, name in (ACCURACY[0], ACCURACY[3]):
        accuracy = frame[(frame.task == task) & (frame.metric == metric) & (frame.k == 20)]
        for other in ("novelty", "catalog_coverage"):
            diversity_task = "new" if task == "discovery" else "repeat"
            values = frame[
                (frame.task == diversity_task) & (frame.metric == other) & (frame.k == 20)
            ]
            joined = accuracy.merge(
                values[["condition", "mean"]],
                on="condition",
                suffixes=("", "_other"),
                validate="one_to_one",
            )
            joined = joined.dropna(subset=["mean", "mean_other"])
            if joined.empty:
                continue
            fig, ax = axes()
            xlabel, scale = metric_label(other, diversity_task)
            ylabel, _ = metric_label(metric, task)
            for n, group in joined.groupby("neighbors"):
                ax.scatter(
                    group.mean_other * scale, group["mean"], s=24, label=f"n = {int(n)}", alpha=0.8
                )
            ax.set(
                xlabel=xlabel.replace("$K$", "20"),
                ylabel=ylabel.replace("$K$", "20"),
                ylim=(0, None),
            )
            ax.legend(frameon=False)
            yield f"{name}_{other}.png", fig


@contextmanager
def draw(frame, config):
    with figure_scope():
        yield _figures(primary(frame, config), config)


def _figures(frame, config):
    yield from draw_curves(frame, config)
    if config.model == "content":
        yield from draw_content_points(frame)
    else:
        yield from draw_grid(frame)
        yield from draw_tradeoffs(frame)
