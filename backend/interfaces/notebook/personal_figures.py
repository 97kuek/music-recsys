"""Compact PNG-only comparison figures; no duplicate source figures."""

from backend.interfaces.notebook.evaluation_figures import observation_figures
from backend.interfaces.notebook.parameter_figures import comparison_curves


def figures(output, rows, common, observations):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    frame, shared = pd.DataFrame(rows), pd.DataFrame(common)
    names = []
    plots = comparison_curves(frame, shared, frame.condition.unique())
    for name, fig in plots:
        try:
            fig.savefig(output / f"{name}.png", dpi=160)
            names.append(f"{name}.png")
        finally:
            plt.close(fig)
    for name, fig in observation_figures(pd.DataFrame(observations)).items():
        try:
            fig.savefig(output / f"{name}.png", dpi=160)
            names.append(f"{name}.png")
        finally:
            plt.close(fig)
    return names
