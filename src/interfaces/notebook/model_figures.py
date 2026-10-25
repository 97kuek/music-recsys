"""Final Test figures using the same K, units and single-metric style as ItemCF/ALS."""

from src.interfaces.notebook.parameter_figures import ACCURACY, DIVERSITY, EFFICIENCY, save
from src.interfaces.notebook.scientific_plots import STYLES, axes, finish_k, metric_label, plot_k


def label(group, model):
    row = group.iloc[0]
    if model == "bpr":
        return f"$f$ = {int(row.factors)}, epochs = {int(row.iterations)}"
    if model == "ease":
        return rf"$\lambda$ = {row.regularization:g}"
    if model == "bm25":
        return rf"$n$ = {int(row.neighbors)}, $k_1$ = {row.k1:g}, $b$ = {row.b:g}"
    return f"IDF {'on' if row.use_idf else 'off'}, {row.aggregation}"


def slices(frame, model):
    if model == "bpr":
        return [
            ("factors", frame[frame.iterations == 500], "factors"),
            ("epochs", frame[frame.factors == 128], "iterations"),
        ]
    if model == "bm25":
        baseline = (frame.k1 == 1.2) & (frame.b == 0.75)
        return [("neighbors", frame[baseline], "neighbors"), ("validation", frame[~baseline], None)]
    return [("", frame, "regularization" if model == "ease" else None)]


def save_figures(output, rows, primary_names, model):
    import matplotlib

    matplotlib.use("Agg")
    import pandas as pd

    frame = pd.DataFrame(rows)
    primary = frame[frame.condition.isin(primary_names)]
    selected = primary[
        primary.window_days.isna()
        | ((primary.window_days == 90) & (primary.day_weight.isna() | (primary.day_weight == 1)))
    ]
    figures = []
    for task, metric, name in (*ACCURACY, *DIVERSITY):
        values = selected[(selected.task == task) & (selected.metric == metric)]
        if not values["mean"].notna().any():
            continue
        ylabel, scale = metric_label(metric, task)
        for suffix, subset, parameter in slices(values, model):
            if subset.empty:
                continue
            fig, ax = axes(figsize=(7.4, 4.4))
            for i, (_, group) in enumerate(subset.groupby("condition", sort=False)):
                plot_k(
                    ax,
                    group,
                    "mean",
                    index=i,
                    label=label(group, model),
                    scale=scale,
                    interval=model != "bpr",
                )
                if model == "bpr":
                    sd = group.sort_values("k").dropna(subset=["seed_sd"])
                    ax.errorbar(
                        sd.k,
                        sd["mean"] * scale,
                        yerr=sd.seed_sd * scale,
                        fmt="none",
                        ecolor=STYLES[i % len(STYLES)]["color"],
                        capsize=2,
                        linewidth=0.7,
                    )
            finish_k(ax, sorted(subset.k.unique()), ylabel)
            figures.append(save(fig, output, f"{name}{'_' + suffix if suffix else ''}_k"))
            points = subset[subset.k == 20].copy()
            if points.empty:
                continue
            fig, ax = axes(figsize=(7.4, 4.0))
            if parameter:
                points = points.sort_values(parameter)
                positions = points[parameter].to_numpy()
                labels = [f"{v:g}" for v in positions]
                xlabel = {
                    "factors": "Number of latent factors, $f$",
                    "iterations": "Training epochs",
                    "neighbors": "Number of item neighbors, $n$",
                    "regularization": r"Regularization, $\lambda$",
                }[parameter]
            else:
                positions = list(range(len(points)))
                labels = [label(points.iloc[[i]], model) for i in range(len(points))]
                xlabel = "Fixed configuration"
            ax.plot(
                positions,
                points["mean"] * scale,
                "o-" if parameter else "o",
                color=STYLES[0]["color"],
                markerfacecolor="white",
            )
            if model == "bpr":
                ax.errorbar(
                    positions,
                    points["mean"] * scale,
                    yerr=points.seed_sd * scale,
                    fmt="none",
                    capsize=3,
                    color=STYLES[0]["color"],
                )
            elif {"ci_low", "ci_high"}.issubset(points):
                valid = points.ci_low.notna() & points.ci_high.notna()
                if valid.any():
                    import numpy as np

                    ci = points[valid]
                    ax.errorbar(
                        np.asarray(positions)[valid],
                        ci["mean"] * scale,
                        yerr=[(ci["mean"] - ci.ci_low) * scale, (ci.ci_high - ci["mean"]) * scale],
                        fmt="none",
                        capsize=3,
                        color=STYLES[0]["color"],
                    )
            ax.set(
                xlabel=xlabel,
                ylabel=ylabel.replace("$K$", "20"),
                xticks=positions,
                xticklabels=labels,
                ylim=(0, None),
            )
            if parameter is None:
                ax.tick_params(axis="x", labelrotation=25, labelsize=8)
            figures.append(save(fig, output, f"{name}{'_' + suffix if suffix else ''}_points"))
    # All pre-existing baseline Tests are shown, without choosing a winner on Test.
    for task, metric, name in ACCURACY:
        values = frame[
            (frame.aspect == "accuracy")
            & (frame.task == task)
            & (frame.metric == metric)
            & (frame.window_days == 90)
            & (frame.k == 20)
            & (frame.day_weight.isna() | (frame.day_weight == 1))
        ].dropna(subset=["mean"])
        if values.empty:
            continue
        ylabel, scale = metric_label(metric, task)
        fig, ax = axes(figsize=(8, max(4, len(values) * 0.35)))
        labels = []
        for i, (_, row) in enumerate(values.iterrows()):
            is_primary = row.condition in primary_names
            labels.append(label(values.iloc[[i]], model) if is_primary else row.condition)
            error = (
                [[(row["mean"] - row.ci_low) * scale], [(row.ci_high - row["mean"]) * scale]]
                if pd.notna(row.get("ci_low")) and pd.notna(row.get("ci_high"))
                else None
            )
            ax.errorbar(
                row["mean"] * scale,
                i,
                xerr=error,
                fmt="o" if is_primary else "D",
                color=STYLES[0]["color"] if is_primary else ".4",
                capsize=3,
            )
        ax.set(
            xlabel=ylabel.replace("$K$", "20"),
            yticks=range(len(values)),
            yticklabels=labels,
            xlim=(0, None),
        )
        ax.invert_yaxis()
        figures.append(save(fig, output, name + "_models"))
    # Small categorical cost figures; unavailable measurements never become zero.
    for _, metric, name in EFFICIENCY:
        values = selected[(selected.task == "all") & (selected.metric == metric)].dropna(
            subset=["mean"]
        )
        if values.empty:
            continue
        fig, ax = axes(figsize=(8, max(3.5, len(values) * 0.32)))
        ax.barh(range(len(values)), values["mean"], color=STYLES[0]["color"])
        ax.set(
            yticks=range(len(values)),
            yticklabels=[label(values.iloc[[i]], model) for i in range(len(values))],
            xlabel=metric.replace("_", " ")
            + (" (s)" if "seconds" in metric or "latency" in metric else ""),
            xlim=(0, None),
        )
        ax.invert_yaxis()
        figures.append(save(fig, output, name))
    from src.interfaces.notebook.evaluation_figures import observation_figures

    observed = primary[(primary.aspect == "observations") & (primary.condition == primary_names[0])]
    if not observed.empty:
        for name, fig in observation_figures(observed).items():
            figures.append(save(fig, output, name))
    return figures
