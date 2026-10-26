"""Title-free ItemCF Test figures: measured K, neighbor counts and categorical models."""

from src.interfaces.notebook.plots import STYLES, axes, finish_k, metric_label
from src.interfaces.notebook.report_data import accuracy_rows, shared_observations


def _style(record):
    return dict(
        color=STYLES[0 if record["similarity"] == "cosine" else 1]["color"],
        marker={20: "o", 50: "s", 100: "^"}.get(record["neighbors"], "D"),
        linestyle={20: "-", 50: "--", 100: ":"}.get(record["neighbors"], "-."),
    )


def _neighbor_figure(frame, ylabel, scale):
    """Numeric spacing for the measured integer counts; no interpolated estimates."""
    from matplotlib.ticker import MaxNLocator

    fig, ax = axes()
    for index, (similarity, rows) in enumerate(frame.groupby("similarity", sort=True)):
        rows = rows.sort_values("neighbors")
        ax.plot(
            rows.neighbors,
            rows["mean"] * scale,
            label=similarity.capitalize(),
            markerfacecolor="white",
            linewidth=1.1,
            markersize=5,
            **STYLES[index],
        )
        if {"ci_low", "ci_high"}.issubset(rows.columns):
            ci = rows.dropna(subset=["mean", "ci_low", "ci_high"])
            if not ci.empty:
                ax.errorbar(
                    ci.neighbors,
                    ci["mean"] * scale,
                    yerr=[(ci["mean"] - ci.ci_low) * scale, (ci.ci_high - ci["mean"]) * scale],
                    fmt="none",
                    ecolor=STYLES[index]["color"],
                    capsize=3,
                    linewidth=0.9,
                )
    ax.set(
        xlabel=r"Number of item neighbors, $n$",
        ylabel=ylabel.replace(r"$K$", "20"),
        xticks=sorted(frame.neighbors.unique()),
        ylim=(0, None),
    )
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    ax.legend(frameon=False)
    return fig


def figures(records):
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import (
        ACCURACY_NAMES,
        efficiency_figures,
        observation_figures,
    )

    items = [r for r in records if "similarity" in r]
    result = {}
    for (task, metric), (name, _) in ACCURACY_NAMES.items():
        ylabel, scale = metric_label(metric, task)
        groups = [(r, accuracy_rows(r, task, metric)) for r in items]
        if not any(f["mean"].notna().any() for _, f in groups):
            continue
        fig, ax = axes(figsize=(6.4, 4.2))
        k20 = []
        for record, frame in groups:
            ax.plot(
                frame.k,
                frame["mean"] * scale,
                label=f"{record['similarity'].capitalize()}, $n$ = {record['neighbors']}",
                markerfacecolor="white",
                linewidth=1.0,
                markersize=3,
                **_style(record),
            )
            k20.extend(
                dict(row, similarity=record["similarity"], neighbors=record["neighbors"])
                for row in frame[frame.k == 20].to_dict("records")
            )
        finish_k(ax, pd.concat([f for _, f in groups]).k, ylabel)
        result[name] = fig
        result[f"{name}_neighbors"] = _neighbor_figure(pd.DataFrame(k20), ylabel, scale)
        if len(records) > len(items):
            result[f"{name}_models"] = _model_figure(records, task, metric)
    for task in ("new", "repeat"):
        for metric, name in (
            ("ild", "ild"),
            ("catalog_coverage", "coverage"),
            ("novelty", "novelty"),
        ):
            ylabel, scale = metric_label(metric)
            groups = [(r, pd.DataFrame(r["result"]["diversity"])) for r in items]
            groups = [(r, f[f.task == task].sort_values("k")) for r, f in groups]
            if not any(f[metric].notna().any() for _, f in groups):
                continue
            fig, ax = axes(figsize=(6.4, 4.2))
            k20 = []
            for record, frame in groups:
                ax.plot(
                    frame.k,
                    frame[metric] * scale,
                    label=f"{record['similarity'].capitalize()}, $n$ = {record['neighbors']}",
                    markerfacecolor="white",
                    linewidth=1.0,
                    markersize=3,
                    **_style(record),
                )
                k20.extend(
                    dict(
                        mean=row[metric],
                        similarity=record["similarity"],
                        neighbors=record["neighbors"],
                    )
                    for row in frame[frame.k == 20].to_dict("records")
                )
            finish_k(ax, pd.concat([f for _, f in groups]).k, ylabel)
            result[f"{name}_{task}"] = fig
            result[f"{name}_{task}_neighbors"] = _neighbor_figure(pd.DataFrame(k20), ylabel, scale)

    observed = shared_observations(records)
    result.update(observation_figures(pd.DataFrame(observed)))
    result.update(
        efficiency_figures(
            pd.DataFrame([dict(r["result"]["efficiency"], experiment=_label(r)) for r in records])
        )
    )
    return result


def _label(record):
    if "similarity" in record:
        return f"{record['similarity'].capitalize()}, $n$ = {record['neighbors']}"
    return {
        "popularity-plays": "Popularity: total play count",
        "popularity-users": "Popularity: unique listener count",
    }[record["condition"]]


def _model_figure(records, task, metric):
    ylabel, scale = metric_label(metric, task)
    fig, ax = axes(figsize=(7.2, max(3.4, len(records) * 0.48)))
    ax.grid(False)
    ax.grid(axis="x", color="0.9", linewidth=0.6)
    for index, record in enumerate(records):
        frame = accuracy_rows(record, task, metric)
        row = frame[frame.k == 20].iloc[0]
        if row["mean"] is None:
            continue
        mean = float(row["mean"]) * scale
        style = _style(record) if "similarity" in record else dict(color="0.3", marker="D")
        error = (
            [[mean - row.ci_low * scale], [row.ci_high * scale - mean]]
            if row[["ci_low", "ci_high"]].notna().all()
            else None
        )
        ax.errorbar(mean, index, xerr=error, fmt=style["marker"], color=style["color"], capsize=3)
    ax.set(
        xlabel=ylabel.replace(r"$K$", "20"),
        yticks=range(len(records)),
        yticklabels=[_label(r) for r in records],
        xlim=(0, None),
    )
    ax.invert_yaxis()
    return fig


def save_figures(output, records):
    from src.interfaces.notebook.plots import save_pngs

    return save_pngs(output, lambda: figures(records))
