"""Stage timing for full-population GPU batches, separate from online latency."""

from backend.infrastructure.research.figures import save
from backend.interfaces.notebook.plots import axes


def figures(output, rows):
    import pandas as pd

    frame = pd.DataFrame(rows)
    warm = frame[frame.repetition > 0].dropna(subset=["seconds"])
    if warm.empty:
        return []
    summary = warm.groupby("stage").seconds.agg(["median", "min", "max"])
    fig, ax = axes(figsize=(9, 4.5))
    ax.barh(
        summary.index,
        summary["median"],
        xerr=[summary["median"] - summary["min"], summary["max"] - summary["median"]],
    )
    ax.set(
        xlabel="Seconds / full population (two tasks)",
        xlim=(0, None),
    )
    return [save(fig, output, "stage_seconds")]
