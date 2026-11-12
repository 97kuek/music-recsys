"""One metric per figure for the frozen same-pool control; missing costs stay absent."""

from backend.infrastructure.research.figures import save
from backend.interfaces.notebook.parameter_figures import comparison_curves
from backend.interfaces.notebook.plots import axes, finish_k, plot_k


def figures(output, rows, common, strata):
    import pandas as pd

    from backend.interfaces.notebook.evaluation_figures import observation_figures

    frame, shared, bands = map(pd.DataFrame, (rows, common, strata))
    names = [
        save(fig, output, name)
        for name, fig in comparison_curves(frame, shared, ("ease-union", "rrf"))
    ]
    costs = frame[frame.aspect == "efficiency"]
    for metric, values in costs.groupby("metric"):
        if not values["mean"].notna().any():
            continue
        fig, ax = axes(figsize=(8, 4.5))
        measured = values.dropna(subset=["mean"])
        ax.barh(measured.condition, measured["mean"])
        ax.set(xlabel=metric, title="Saved RRF cost; EASE union rescoring unmeasured")
        names.append(save(fig, output, metric))
    for task in ("new", "repeat"):
        for kind in ("history", "popularity"):
            for condition, label in (
                ("rrf-minus-ease", "Recall difference (percentage points)"),
                ("rrf-bpr-only", "Recall from BPR-only candidates (%)"),
            ):
                values = bands[
                    (bands.task == task)
                    & (bands.stratum_kind == kind)
                    & (bands.condition == condition)
                    & (bands.window_days == 90)
                ]
                if not values["mean"].notna().any():
                    continue
                fig, ax = axes(figsize=(8, 4.5))
                for i, band in enumerate(("Q1", "Q2", "Q3", "Q4")):
                    subset = values[values.stratum == band]
                    plot_k(ax, subset, "mean", index=i, label=band, scale=100, interval=False)
                if condition == "rrf-minus-ease":
                    ax.axhline(0, color="grey", linewidth=0.8)
                finish_k(ax, sorted(values.k.unique()), label)
                if condition == "rrf-minus-ease":
                    ax.autoscale(enable=True, axis="y")
                ax.set_title(f"{task} / {kind} / 90 days")
                suffix = "diff" if condition == "rrf-minus-ease" else "added"
                names.append(save(fig, output, f"{task}_{kind}_{suffix}"))
    observed = frame[(frame.aspect == "observations") & (frame.condition == "ease-union")]
    names.extend(save(fig, output, name) for name, fig in observation_figures(observed).items())
    return names
