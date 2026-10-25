"""One-metric ALS Test figures with measured factor counts and K cutoffs."""

from src.interfaces.notebook.scientific_plots import STYLES, axes, finish_k, metric_label


def _label(record):
    if "factors" in record:
        return f"ALS, $f$ = {record['factors']}"
    name = record["condition"]
    if name.startswith("itemcf-"):
        _, similarity, neighbors = name.split("-")
        return f"ItemCF: {similarity.capitalize()}, $n$ = {neighbors}"
    return {
        "popularity-plays": "Popularity: total play count",
        "popularity-users": "Popularity: unique listener count",
    }[name]


def _factor_figure(frame, ylabel, scale):
    from matplotlib.ticker import MaxNLocator

    frame = frame.sort_values("factors")
    fig, ax = axes()
    ax.plot(
        frame.factors,
        frame["mean"] * scale,
        markerfacecolor="white",
        linewidth=1.1,
        markersize=5,
        **STYLES[0],
    )
    if {"ci_low", "ci_high"}.issubset(frame.columns):
        ci = frame.dropna(subset=["mean", "ci_low", "ci_high"])
        if not ci.empty:
            ax.errorbar(
                ci.factors,
                ci["mean"] * scale,
                yerr=[(ci["mean"] - ci.ci_low) * scale, (ci.ci_high - ci["mean"]) * scale],
                fmt="none",
                ecolor=STYLES[0]["color"],
                capsize=3,
                linewidth=0.9,
            )
    ax.set(
        xlabel=r"Number of latent factors, $f$",
        ylabel=ylabel.replace(r"$K$", "20"),
        xticks=sorted(frame.factors.unique()),
        ylim=(0, None),
    )
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    return fig


def _model_figure(records, task, metric):
    from src.interfaces.notebook.itemcf_test_figures import _accuracy

    ylabel, scale = metric_label(metric, task)
    fig, ax = axes(figsize=(7.2, max(3.6, len(records) * 0.42)))
    ax.grid(False)
    ax.grid(axis="x", color="0.9", linewidth=0.6)
    for index, record in enumerate(records):
        row = _accuracy(record, task, metric).query("k==20").iloc[0]
        if row[["mean"]].isna().any():
            continue
        mean = float(row["mean"]) * scale
        error = (
            [[mean - row.ci_low * scale], [row.ci_high * scale - mean]]
            if row[["ci_low", "ci_high"]].notna().all()
            else None
        )
        ax.errorbar(
            mean,
            index,
            xerr=error,
            fmt="o" if "factors" in record else "D",
            color=STYLES[0]["color"] if "factors" in record else "0.4",
            capsize=3,
        )
    ax.set(
        xlabel=ylabel.replace(r"$K$", "20"),
        yticks=range(len(records)),
        yticklabels=[_label(r) for r in records],
        xlim=(0, None),
    )
    ax.invert_yaxis()
    return fig


def figures(records):
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import (
        ACCURACY_NAMES,
        efficiency_figures,
        observation_figures,
    )
    from src.interfaces.notebook.itemcf_test_figures import _accuracy

    primary = sorted([r for r in records if "factors" in r], key=lambda r: r["factors"])
    styles = (*STYLES, dict(color="0.25", marker="v", linestyle=(0, (3, 1, 1, 1))))
    result = {}
    for (task, metric), (name, _) in ACCURACY_NAMES.items():
        ylabel, scale = metric_label(metric, task)
        groups = [(r, _accuracy(r, task, metric)) for r in primary]
        if not any(f["mean"].notna().any() for _, f in groups):
            continue
        fig, ax = axes()
        k20 = []
        for index, (record, frame) in enumerate(groups):
            ax.plot(
                frame.k,
                frame["mean"] * scale,
                label=f"$f$ = {record['factors']}",
                markerfacecolor="white",
                markersize=3,
                linewidth=1,
                **styles[index % len(styles)],
            )
            k20.extend(
                dict(row, factors=record["factors"])
                for row in frame.query("k==20").to_dict("records")
            )
        finish_k(ax, pd.concat([f for _, f in groups]).k, ylabel)
        result[name] = fig
        result[f"{name}_factors"] = _factor_figure(pd.DataFrame(k20), ylabel, scale)
        if len(records) > len(primary):
            result[f"{name}_models"] = _model_figure(records, task, metric)
    for task in ("new", "repeat"):
        for metric, name in (
            ("ild", "ild"),
            ("catalog_coverage", "coverage"),
            ("novelty", "novelty"),
        ):
            ylabel, scale = metric_label(metric)
            groups = [
                (r, pd.DataFrame(r["result"]["diversity"]).query("task==@task").sort_values("k"))
                for r in primary
            ]
            if not any(f[metric].notna().any() for _, f in groups):
                continue
            fig, ax = axes()
            k20 = []
            for index, (record, frame) in enumerate(groups):
                ax.plot(
                    frame.k,
                    frame[metric] * scale,
                    label=f"$f$ = {record['factors']}",
                    markerfacecolor="white",
                    markersize=3,
                    linewidth=1,
                    **styles[index % len(styles)],
                )
                k20.extend(
                    dict(mean=row[metric], factors=record["factors"])
                    for row in frame.query("k==20").to_dict("records")
                )
            finish_k(ax, pd.concat([f for _, f in groups]).k, ylabel)
            result[f"{name}_{task}"] = fig
            result[f"{name}_{task}_factors"] = _factor_figure(pd.DataFrame(k20), ylabel, scale)

    def observations(record):
        return sorted(
            record["result"]["observations"],
            key=lambda row: (
                row["phase"],
                row["window_days"],
                row["task"],
                row["observation"],
                row["bin"],
            ),
        )

    observed = observations(primary[0])
    if any(observations(r) != observed for r in records):
        raise ValueError("Test observation distributions differ")
    result.update(observation_figures(pd.DataFrame(observed)))
    result.update(
        efficiency_figures(
            pd.DataFrame([dict(r["result"]["efficiency"], experiment=_label(r)) for r in records])
        )
    )
    return result


def save_figures(output, records):
    from src.interfaces.notebook.scientific_plots import save_pngs

    return save_pngs(output, lambda: figures(records))
