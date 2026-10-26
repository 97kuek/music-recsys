"""1指標・1Axes・1ファイルの共通描画。"""

ACCURACY_NAMES = {
    ("discovery", "binary_ndcg"): ("binary_ndcg", "Binary NDCG"),
    ("discovery", "recall"): ("recall", "Recall"),
    ("retention_proxy", "graded_ndcg"): ("graded_ndcg_new", "Graded NDCG · New"),
    ("repeat", "graded_ndcg"): ("graded_ndcg_repeat", "Graded NDCG · Repeat"),
    ("repeat", "repeat_recall"): ("repeat_recall", "Repeat Recall"),
}


def factor_figures(frame, model, window_days):
    """Readable within-model comparisons; no baseline selection or metric changes."""
    import matplotlib.pyplot as plt

    figures = {}
    styles = [
        ("#0072B2", "-", "o"),
        ("#D55E00", "--", "s"),
        ("#009E73", "-.", "^"),
        ("#CC79A7", ":", "D"),
    ]
    rows = frame[frame.model == model]
    for task, metric, name, title in (
        ("discovery", "binary_ndcg", "binary_ndcg_factors", "Binary NDCG@K"),
        ("discovery", "recall", "recall_factors", "Recall@K"),
        ("repeat", "repeat_recall", "repeat_recall_factors", "Repeat Recall@K"),
        ("all", "latency_mean", "latency_mean_all_factors", "Recommendation Latency"),
        ("all", "training_seconds", "training_seconds_all_factors", "Training Time"),
    ):
        subset = rows[(rows.task == task) & (rows.metric == metric)].dropna(subset=["mean"])
        if subset.empty:
            continue
        fig, ax = plt.subplots(figsize=(11, 6.5), layout="constrained")
        if task == "all":
            subset = subset.sort_values("factors")
            scale = 1000 if metric == "latency_mean" else 1
            bars = ax.bar(
                [str(int(f)) for f in subset.factors],
                subset["mean"] * scale,
                color=[styles[i % len(styles)][0] for i in range(len(subset))],
            )
            ax.bar_label(bars, fmt="%.3f" if scale == 1000 else "%.1f", fontsize=14, padding=5)
            ax.set_xlabel("Factors", fontsize=16)
            ax.set_ylabel("ms / recommendation" if scale == 1000 else "Seconds", fontsize=14)
            ax.margins(y=0.15)
        else:
            for i, (factors, group) in enumerate(subset.groupby("factors", sort=True)):
                color, line, marker = styles[i % len(styles)]
                group = group.sort_values("k")
                ax.plot(
                    group.k,
                    group["mean"],
                    label=f"{int(factors)} factors",
                    color=color,
                    linestyle=line,
                    marker=marker,
                    markevery=(i % 4, 4),
                    markersize=6,
                    linewidth=2.5,
                )
            ax.set_xlabel("Recommended tracks (K)", fontsize=16)
            ax.set_ylabel(
                title.replace("@K", "") + (" (zoomed scale)" if metric == "binary_ndcg" else ""),
                fontsize=16,
            )
            if metric != "binary_ndcg":
                ax.set_ylim(bottom=0)
            ax.set_xticks(range(2, 21, 2))
            ax.legend(fontsize=14, loc="best")
        ax.set_title(f"{model.upper()}: {title}", fontsize=22, pad=18)
        ax.tick_params(labelsize=13)
        ax.grid(axis="y" if task == "all" else "both", alpha=0.25)
        caption = f"Validation {window_days} days · identical split and candidate rules"
        if metric == "latency_mean":
            caption += "\nGPU latency is batch-amortized; CPU latency is measured per request"
        fig.supxlabel(caption, fontsize=11)
        figures[name] = fig
    return figures


def accuracy_figures(frame):
    from src.interfaces.notebook.plots import axes, finish_k, metric_label, plot_k

    figures = {}
    for (task, metric), rows in frame.groupby(["task", "metric"], sort=False):
        if not rows["mean"].notna().any():
            continue
        name, _ = ACCURACY_NAMES[(task, metric)]
        ylabel, scale = metric_label(metric, task)
        fig, ax = axes()
        groups = rows.groupby("experiment", sort=False) if "experiment" in rows else [(None, rows)]
        for index, (label, values) in enumerate(groups):
            plot_k(ax, values, "mean", index=index, label=label, scale=scale, interval=True)
        finish_k(ax, rows.k, ylabel)
        figures[name] = fig
    return figures


def diversity_figures(frame, *, separate_tasks=False):
    from src.interfaces.notebook.plots import axes, finish_k, metric_label, plot_k

    figures = {}
    if separate_tasks:
        for task, rows in frame.groupby("task", sort=False):
            for name, fig in diversity_figures(rows).items():
                figures[f"{name}_{task}"] = fig
        return figures
    fields = ["experiment", "task"] if "experiment" in frame else ["task"]
    for metric, name in [
        ("ild", "ild"),
        ("catalog_coverage", "coverage"),
        ("novelty", "novelty"),
    ]:
        if not frame[metric].notna().any():
            continue
        ylabel, scale = metric_label(metric)
        fig, ax = axes()
        for index, (label, group) in enumerate(frame.groupby(fields, sort=False)):
            if group[metric].notna().any():
                # With one task, the filename/caption identifies the task.
                if "experiment" in frame and frame.task.nunique() == 1:
                    label = label[0]
                plot_k(ax, group, metric, index=index, label=label, scale=scale)
        finish_k(ax, frame.k, ylabel)
        figures[name] = fig
    return figures


def efficiency_figures(frame):
    from src.interfaces.notebook.plots import STYLES, axes, label_text

    figures = {}
    for metric, name, unit, scale in [
        ("latency_mean", "latency", "Ranking time (ms / recommendation)", 1000),
        ("input_tokens", "input_tokens", "Input tokens per run", 1),
        ("output_tokens", "output_tokens", "Output tokens per run", 1),
        ("api_cost", "api_cost", "API cost (USD / run)", 1),
        ("llm_calls", "llm_calls", "LLM calls per run", 1),
        ("tool_calls", "tool_calls", "Tool calls per run", 1),
    ]:
        valid = frame.dropna(subset=[metric])
        if valid.empty:
            continue
        fig, ax = axes(figsize=(6.4, max(2.8, len(valid) * 0.5)))
        label = "experiment" if "experiment" in frame else "model"
        ax.barh(
            [label_text(v) for v in valid[label]],
            valid[metric] * scale,
            color=[STYLES[i % len(STYLES)]["color"] for i in range(len(valid))],
        )
        ax.set(xlabel=unit, xlim=(0, None))
        ax.grid(False)
        ax.grid(axis="x", color="0.9", linewidth=0.6)
        figures[name] = fig
    return figures


def observation_figures(frame):
    from matplotlib.ticker import MaxNLocator

    from src.interfaces.notebook.plots import STYLES, axes, label_text

    figures = {}
    if frame.empty:
        return figures
    for column in ("window_days", "phase"):
        if column in frame and frame[column].nunique() > 1:
            raise ValueError("Plot one observation window and phase at a time")
    bins = ["1", "2", "3–4", "5–9", "10–19", "20+"]
    for metric, name in [
        ("evaluation_play_count", "play_count"),
        ("evaluation_active_days", "active_days"),
    ]:
        if not frame.loc[frame.observation == metric, "pairs"].notna().any():
            continue
        groups = []
        for index, task in enumerate(("new", "repeat")):
            rows = frame[(frame.observation == metric) & (frame.task == task)].set_index("bin")
            if rows.empty:
                continue
            counts = rows.pairs.reindex(bins)
            if counts.isna().any():
                raise ValueError("Observation bins must be measured; missing is not zero")
            total = counts.sum()
            if total == 0:
                continue  # A proportion with denominator zero is undefined.
            groups.append((index, task, counts, total))
        if not groups:
            continue
        fig, ax = axes()
        for index, task, counts, total in groups:
            ax.bar(
                [i + (index - 0.5) * 0.36 for i in range(len(bins))],
                counts / total * 100,
                width=0.36,
                label=f"{label_text(task)} ($n$ = {int(total):,})",
                color=STYLES[index]["color"],
                hatch="" if index == 0 else "///",
                edgecolor="white",
                linewidth=0.5,
            )
        ax.set_xticks(range(len(bins)), bins)
        ax.set(
            xlabel="Plays per user–track pair (binned)"
            if name == "play_count"
            else "Listening days per user–track pair (binned)",
            ylabel="User–track pairs within each task (%)",
            ylim=(0, None),
        )
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
        ax.legend(frameon=False, fontsize=9)
        figures[name] = fig
    return figures


def show_figures(figures):
    import matplotlib.pyplot as plt
    from IPython.display import display

    for figure in figures.values():
        display(figure)
        plt.close(figure)


def accuracy_heatmaps(frame, day_weight):
    """各指標の K × 評価期間 / 各セルは実測した整数Kのみ。"""
    import numpy as np

    from src.interfaces.notebook.plots import axes, metric_label

    figures = {}
    for (task, metric), rows in frame.groupby(["task", "metric"], sort=False):
        if metric == "graded_ndcg":
            rows = rows[rows.day_weight == day_weight]
        if not rows["mean"].notna().any():
            continue
        values = rows.pivot(index="window_days", columns="k", values="mean").sort_index()
        name, _ = ACCURACY_NAMES[task, metric]
        ylabel, scale = metric_label(metric, task)
        fig, ax = axes(figsize=(7.2, 2.8))
        ax.grid(False)
        data = values.to_numpy(float) * scale
        finite = data[np.isfinite(data)]
        vmax = max(float(finite.max()), 1e-9) if finite.size else 1.0
        picture = ax.imshow(
            np.ma.masked_invalid(data),
            aspect="auto",
            vmin=0,
            vmax=vmax,
            cmap="viridis",
            interpolation="nearest",
        )
        ax.set(
            xticks=range(len(values.columns)),
            xticklabels=values.columns,
            yticks=range(len(values.index)),
            yticklabels=values.index,
            xlabel=r"Recommendation cutoff, $K$",
            ylabel="Observation window (days)",
        )
        fig.colorbar(picture, ax=ax, label=ylabel)
        figures[name + "_heatmap"] = fig
    return figures


def prune_generated_figures(output, filenames):
    """共通生成図のうち今回未測定になったものを除去 / 独自の図には触れない。"""
    from pathlib import Path

    accuracy = {name for name, _ in ACCURACY_NAMES.values()}
    known = (
        accuracy
        | {name + "_heatmap" for name in accuracy}
        | {
            "ild",
            "coverage",
            "novelty",
            "latency",
            "input_tokens",
            "output_tokens",
            "api_cost",
            "llm_calls",
            "tool_calls",
            "play_count",
            "active_days",
        }
    )
    for name in known:
        filename = name + ".png"
        if filename not in filenames:
            (Path(output) / filename).unlink(missing_ok=True)
