"""1指標・1Axes・1ファイルの共通描画。"""

ACCURACY_NAMES = {
    ("discovery", "binary_ndcg"): ("binary_ndcg", "Binary NDCG"),
    ("discovery", "recall"): ("recall", "Recall"),
    ("retention_proxy", "graded_ndcg"): ("graded_ndcg_new", "Graded NDCG · New"),
    ("repeat", "graded_ndcg"): ("graded_ndcg_repeat", "Graded NDCG · Repeat"),
    ("repeat", "repeat_recall"): ("repeat_recall", "Repeat Recall"),
}


def accuracy_figures(frame):
    import matplotlib.pyplot as plt

    figures = {}
    for (task, metric), rows in frame.groupby(["task", "metric"], sort=False):
        if not rows["mean"].notna().any():
            continue
        name, title = ACCURACY_NAMES[(task, metric)]
        fig, ax = plt.subplots(figsize=(6, 3.6))
        groups = rows.groupby("experiment", sort=False) if "experiment" in rows else [(None, rows)]
        for label, values in groups:
            values = values.sort_values("k")
            line = ax.plot(
                values.k, values["mean"], marker="o", markersize=3, linewidth=0.8, label=label
            )[0]
            ci = values.dropna(subset=["ci_low", "ci_high"])
            ax.fill_between(
                ci.k.to_numpy(float),
                ci.ci_low.to_numpy(float),
                ci.ci_high.to_numpy(float),
                color=line.get_color(),
                alpha=0.12,
            )
        upper = rows[["mean", "ci_high"]].max().max()
        upper = min(1.0, float(upper) * 1.1) if upper > 0 else 1.0
        ax.set(
            title=title,
            xlabel="Recommended tracks (K)",
            ylabel="Score",
            ylim=(0, upper),
            xticks=list(range(2, 21, 2)),
        )
        if "experiment" in rows:
            ax.legend(fontsize=8)
        fig.tight_layout()
        figures[name] = fig
    return figures


def diversity_figures(frame):
    import matplotlib.pyplot as plt

    figures = {}
    fields = ["experiment", "task"] if "experiment" in frame else ["task"]
    for metric, name, title in [
        ("ild", "ild", "ILD"),
        ("catalog_coverage", "coverage", "Catalog Coverage"),
        ("novelty", "novelty", "Novelty"),
    ]:
        if not frame[metric].notna().any():
            continue
        fig, ax = plt.subplots(figsize=(6, 3.6))
        for label, group in frame.groupby(fields, sort=False):
            group = group.sort_values("k")
            if group[metric].notna().any():
                ax.plot(
                    group.k,
                    group[metric],
                    marker="o",
                    markersize=3,
                    linewidth=0.8,
                    label=" / ".join(label) if isinstance(label, tuple) else label,
                )
        if ax.lines:
            ax.legend(fontsize=8)
        ax.set(title=title, xlabel="K", xticks=list(range(2, 21, 2)))
        fig.tight_layout()
        figures[name] = fig
    return figures


def efficiency_figures(frame):
    import matplotlib.pyplot as plt

    figures = {}
    for metric, name, title, unit, scale in [
        ("latency_mean", "latency", "Latency", "ms / request", 1000),
        ("input_tokens", "input_tokens", "Input Tokens", "tokens / run", 1),
        ("output_tokens", "output_tokens", "Output Tokens", "tokens / run", 1),
        ("api_cost", "api_cost", "API Cost", "USD / run", 1),
        ("llm_calls", "llm_calls", "LLM Calls", "calls / run", 1),
        ("tool_calls", "tool_calls", "Tool Calls", "calls / run", 1),
    ]:
        valid = frame.dropna(subset=[metric])
        if valid.empty:
            continue
        fig, ax = plt.subplots(figsize=(6, max(2.8, len(valid) * 0.5)))
        label = "experiment" if "experiment" in frame else "model"
        ax.barh(valid[label], valid[metric] * scale)
        ax.set(title=title, xlabel=unit)
        fig.tight_layout()
        figures[name] = fig
    return figures


def observation_figures(frame):
    import matplotlib.pyplot as plt

    figures = {}
    if frame.empty:
        return figures
    bins = ["1", "2", "3–4", "5–9", "10–19", "20+"]
    for metric, name, title in [
        ("evaluation_play_count", "play_count", "Play Count"),
        ("evaluation_active_days", "active_days", "Active Days"),
    ]:
        if not frame.loc[frame.observation == metric, "pairs"].notna().any():
            continue
        fig, ax = plt.subplots(figsize=(6, 3.6))
        for index, task in enumerate(("new", "repeat")):
            rows = frame[(frame.observation == metric) & (frame.task == task)].set_index("bin")
            counts = rows.pairs.reindex(bins, fill_value=0)
            ax.bar(
                [i + (index - 0.5) * 0.35 for i in range(len(bins))], counts, width=0.35, label=task
            )
        ax.set_xticks(range(len(bins)), bins)
        ax.set(
            title=title,
            xlabel="Plays per user-track"
            if name == "play_count"
            else "Listening days per user-track",
            ylabel="User-track pairs",
        )
        ax.legend()
        fig.tight_layout()
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
    import matplotlib.pyplot as plt
    import numpy as np

    figures = {}
    for (task, metric), rows in frame.groupby(["task", "metric"], sort=False):
        if metric == "graded_ndcg":
            rows = rows[rows.day_weight == day_weight]
        if not rows["mean"].notna().any():
            continue
        values = rows.pivot(index="window_days", columns="k", values="mean").sort_index()
        name, title = ACCURACY_NAMES[task, metric]
        fig, ax = plt.subplots(figsize=(9, 3))
        data = values.to_numpy(float)
        finite = data[np.isfinite(data)]
        vmax = max(float(finite.max()), 1e-9) if finite.size else 1.0
        picture = ax.imshow(data, aspect="auto", vmin=0, vmax=vmax, cmap="viridis")
        ax.set(
            xticks=range(len(values.columns)),
            xticklabels=values.columns,
            yticks=range(len(values.index)),
            yticklabels=values.index,
            xlabel="Recommended tracks (K)",
            ylabel="Evaluation days",
            title=title,
        )
        fig.colorbar(picture, ax=ax, label="Score")
        fig.tight_layout()
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
