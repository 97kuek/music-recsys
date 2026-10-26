"""Draw full evaluation figures from prepared tables without reading or writing files."""

from contextlib import contextmanager

from src.interfaces.notebook.plots import figure_scope
from src.interfaces.notebook.report_data import primary_accuracy


@contextmanager
def draw(config, frame, beyond, efficiency, observations):
    from src.interfaces.notebook.evaluation_figures import (
        accuracy_figures,
        accuracy_heatmaps,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )

    primary = primary_accuracy(
        frame,
        window=config.evaluation.primary_window_days,
        day_weight=config.evaluation.primary_day_weight,
    )
    with figure_scope():
        figures = accuracy_figures(primary)
        figures.update(diversity_figures(beyond))
        figures.update(efficiency_figures(efficiency))
        figures.update(observation_figures(observations))
        figures.update(accuracy_heatmaps(frame, config.evaluation.primary_day_weight))
        yield {f"{name}.png": figure for name, figure in figures.items()}
