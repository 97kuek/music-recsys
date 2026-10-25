"""Title-free, single-metric ALS figures for manuscript export."""

from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES, observation_figures
from src.interfaces.notebook.scientific_plots import STYLES, axes, finish_k, metric_label

STYLES = (
    *STYLES,
    dict(color="#666666", marker="v", linestyle=(0, (3, 1, 1, 1))),
    dict(color="#8C510A", marker="P", linestyle=(0, (5, 2))),
)


def factor_plot(frame, ylabel, scale=1, *, interval=False):
    """Discrete measured factors on an explicit log2 axis; no interpolating line."""
    from matplotlib.ticker import FixedFormatter, FixedLocator, NullLocator

    frame = frame.sort_values("factors")
    if not frame["mean"].notna().any():
        return None
    fig, ax = axes(figsize=(6.4, 4.0))
    ax.plot(
        frame.factors,
        frame["mean"] * scale,
        "o",
        color=STYLES[0]["color"],
        markerfacecolor="white",
        markersize=5,
    )
    if interval:
        valid = frame.dropna(subset=["mean", "ci_low", "ci_high"])
        if not valid.empty:
            ax.errorbar(
                valid.factors,
                valid["mean"] * scale,
                yerr=[
                    (valid["mean"] - valid.ci_low) * scale,
                    (valid.ci_high - valid["mean"]) * scale,
                ],
                fmt="none",
                ecolor=STYLES[0]["color"],
                capsize=3,
                linewidth=1,
            )
    factors = sorted(frame.factors.unique())
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(factors))
    ax.xaxis.set_major_formatter(FixedFormatter([str(int(f)) for f in factors]))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set(xlabel=r"Number of latent factors, $f$ (log$_2$ scale)", ylabel=ylabel, ylim=(0, None))
    ax.margins(x=0.08)
    return fig


def _accuracy(record, task, metric):
    import pandas as pd

    frame = pd.DataFrame(record["result"]["accuracy"])
    return frame[
        (frame.task == task)
        & (frame.metric == metric)
        & (frame.window_days == 90)
        & (frame.day_weight.isna() | (frame.day_weight == 1))
    ].sort_values("k")


def group_figures(group, ild):
    import pandas as pd

    result = {}
    for (task, metric), (name, _) in ACCURACY_NAMES.items():
        ylabel, scale = metric_label(metric, task)
        k20, curves = [], []
        for record in group:
            frame = _accuracy(record, task, metric)
            k20.extend(
                dict(row, factors=record["factors"])
                for row in frame.query("k==20").to_dict("records")
            )
            curves.append((record["factors"], frame))
        fig = factor_plot(pd.DataFrame(k20), ylabel.replace(r"$K$", "20"), scale, interval=True)
        if fig is None:
            continue
        result[name] = fig
        fig, ax = axes()
        for i, (factors, frame) in enumerate(curves):
            ax.plot(
                frame.k,
                frame["mean"] * scale,
                label=f"$f$ = {factors}",
                markersize=3,
                linewidth=1,
                markerfacecolor="white",
                **STYLES[i],
            )
        finish_k(ax, pd.concat([frame for _, frame in curves]).k, ylabel)
        result[f"{name}_k"] = fig
    for task in ("new", "repeat"):
        for metric, name in (
            ("catalog_coverage", "coverage"),
            ("novelty", "novelty"),
            ("ild", "ild"),
        ):
            ylabel, scale = metric_label(metric)
            k20, curves = [], []
            for record in group:
                if metric == "ild":
                    frame = pd.DataFrame(ild)
                    frame = frame[(frame.factors == record["factors"]) & (frame.task == task)]
                else:
                    frame = pd.DataFrame(record["result"]["diversity"])
                    frame = frame[frame.task == task].rename(columns={metric: "mean"})
                frame = frame.sort_values("k")
                k20.extend(
                    dict(row, factors=record["factors"])
                    for row in frame.query("k==20").to_dict("records")
                )
                curves.append((record["factors"], frame))
            if metric == "ild":
                ylabel = "Common-cohort " + ylabel[0].lower() + ylabel[1:]
            fig = factor_plot(pd.DataFrame(k20), ylabel.replace(r"$K$", "20"), scale)
            if fig is None:
                continue
            result[f"{name}_{task}"] = fig
            fig, ax = axes()
            for i, (factors, frame) in enumerate(curves):
                ax.plot(
                    frame.k,
                    frame["mean"] * scale,
                    label=f"$f$ = {factors}",
                    markerfacecolor="white",
                    markersize=3,
                    linewidth=1,
                    **STYLES[i],
                )
            finish_k(ax, pd.concat([frame for _, frame in curves]).k, ylabel)
            result[f"{name}_{task}_k"] = fig
    for metric, name, label, scale in (
        ("training_seconds", "training", "Training time (s)", 1),
        ("latency_mean", "latency", "Amortized ranking time (ms / user–task)", 1000),
        ("evaluation_seconds", "evaluation", "Evaluation time (s)", 1),
    ):
        frame = pd.DataFrame(
            [dict(factors=r["factors"], mean=r["result"]["efficiency"].get(metric)) for r in group]
        )
        fig = factor_plot(frame, label, scale)
        if fig is not None:
            result[name] = fig
    observed = group[0]["result"]["observations"]

    # Order may differ across implementations; underlying counts must agree
    def canonical(rows):
        return sorted(
            rows,
            key=lambda r: (r["phase"], r["window_days"], r["task"], r["observation"], r["bin"]),
        )

    if any(canonical(r["result"]["observations"]) != canonical(observed) for r in group):
        raise ValueError("Observation distributions differ within the comparison")
    result.update(observation_figures(pd.DataFrame(observed)))
    return result


def sensitivity_figures(records):
    """Show actual seed runs at the recorded alpha/regularization cells; blanks stay blank."""
    import numpy as np
    import pandas as pd

    selected = [
        r
        for r in records
        if r["phase"] == "validation" and r["condition"].startswith("sensitivity/")
    ]
    result = {}
    for (task, metric), (name, _) in ACCURACY_NAMES.items():
        rows = [
            dict(
                alpha=r["alpha"],
                regularization=r["regularization"],
                seed=r["seed"],
                mean=_accuracy(r, task, metric).query("k==20")["mean"].iloc[0],
            )
            for r in selected
        ]
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        # A fixed factor/solver/iteration setting; unlike factors, seed runs are replicates
        signature = {
            (
                r["factors"],
                json_parameters(r),
                r["saved"]["features_sha256"],
                r["saved"]["manifest"]["files"]["learning"]["sha256"],
            )
            for r in selected
        }
        if len(signature) != 1 or frame.duplicated(["alpha", "regularization", "seed"]).any():
            raise ValueError("Sensitivity conditions differ beyond alpha/regularization/seed")
        ylabel, scale = metric_label(metric, task)
        alphas, regs = sorted(frame.alpha.unique()), sorted(frame.regularization.unique())
        grid = np.full((len(regs), len(alphas)), np.nan)
        texts = {}
        for (alpha, reg), values in frame.groupby(["alpha", "regularization"]):
            if set(values.seed) != {42, 43, 44}:
                raise ValueError("Sensitivity cells require all three recorded seeds")
            i, j = regs.index(reg), alphas.index(alpha)
            grid[i, j] = values["mean"].mean() * scale
            texts[i, j] = f"{grid[i, j]:.3f}"
        if not np.isfinite(grid).any():
            continue
        fig, ax = axes(figsize=(6.4, 4.0))
        ax.grid(False)
        artist = ax.imshow(
            np.ma.masked_invalid(grid),
            cmap="cividis",
            aspect="auto",
            vmin=0,
            interpolation="nearest",
        )
        ax.set(
            xticks=range(len(alphas)),
            xticklabels=[f"{v:g}" for v in alphas],
            yticks=range(len(regs)),
            yticklabels=[f"{v:g}" for v in regs],
            xlabel=r"Confidence scaling, $\alpha$ (tested levels)",
            ylabel=r"Regularization, $\lambda$ (tested levels)",
        )
        for i in range(len(regs)):
            for j in range(len(alphas)):
                value = grid[i, j]
                color = "white" if np.isfinite(value) and artist.norm(value) < 0.5 else "black"
                ax.text(
                    j, i, texts.get((i, j), "—"), ha="center", va="center", color=color, fontsize=10
                )
        fig.colorbar(artist, ax=ax, label=ylabel.replace(r"$K$", "20"))
        result[f"{name}_sensitivity"] = fig
    return result


def json_parameters(record):
    import json

    return json.dumps(
        {k: v for k, v in record["parameters"].items() if k not in {"alpha", "regularization"}},
        sort_keys=True,
    )


def save_figures(output, groups, records, ild):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def save(name, figure):
        with matplotlib.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42}):
            for suffix in ("png", "pdf"):
                figure.savefig(output / f"{name}.{suffix}", dpi=300, metadata={"Title": ""})

    names = []
    for phase, group in groups.items():
        drawn = group_figures(group, [r for r in ild if r["phase"] == phase])
        try:
            for name, fig in drawn.items():
                name = f"{name}_{phase}"
                save(name, fig)
                names.append(name)
        finally:
            for fig in drawn.values():
                plt.close(fig)
    drawn = sensitivity_figures(records)
    try:
        for name, fig in drawn.items():
            save(name, fig)
            names.append(name)
    finally:
        for fig in drawn.values():
            plt.close(fig)
    return names
