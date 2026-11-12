"""One metric per figure for local selectors, retaining the frozen display baseline."""

from backend.infrastructure.research.figures import save
from backend.interfaces.notebook.evaluation_figures import observation_figures
from backend.interfaces.notebook.parameter_figures import comparison_curves
from backend.interfaces.notebook.plots import axes


def figures(output, rows, common, observations):
    import matplotlib

    matplotlib.use("Agg")
    import pandas as pd

    frame, shared = pd.DataFrame(rows), pd.DataFrame(common)
    names = [
        save(fig, output, name)
        for name, fig in comparison_curves(frame, shared, frame.condition.unique())
    ]
    values = frame[frame.metric == "selection_latency_mean"].dropna(subset=["mean"])
    if not values.empty:
        fig, ax = axes(figsize=(8, 4.5))
        ax.barh(values.condition, values["mean"] * 1e6)
        ax.set(xlabel="Local selection only (µs/request)")
        names.append(save(fig, output, "selection"))
    for name, fig in observation_figures(pd.DataFrame(observations)).items():
        names.append(save(fig, output, name))
    return names
