"""Prepare sensitivity tables and persist ordinary or publication reports."""

from backend.infrastructure.research.configuration import load_settings
from backend.infrastructure.research.reports import FigureWriter


class SensitivityReport:
    def __init__(self, draw, publication_draw):
        self._ordinary = FigureWriter(draw, dpi=150)
        self._publication = FigureWriter(publication_draw, dpi=300, pdf=True)

    def write(self, config, finished, output):
        if load_settings(config.path).get("search", {}).get("publication", False):
            frame = publication_tables(config, finished, output)
            return self._publication.write(output, frame, config)
        frame = sensitivity_frame(config, finished)
        frame.to_csv(output / "metrics.csv", index=False)
        return self._ordinary.write(output, frame, config)


def sensitivity_frame(config, finished):
    import pandas as pd

    from backend.infrastructure.research.comparison import metric_rows
    from backend.infrastructure.research.parameters import condition_name

    rows = []
    for name, trial, result, source in finished:
        label = condition_name(config.model, trial.parameters, trial.seeds[0])
        if config.model in {"als", "bpr"}:
            label = label.rsplit("-s", 1)[0]
        metadata = dict(
            condition=name, parameters=label, seed=trial.seeds[0], control=source["historical"]
        )
        # Save every period/day-weight, while plotting the shared primary display period.
        rows.extend(dict(**metadata, **row) for row in result["accuracy"])
        rows.extend(
            dict(**metadata, **row)
            for row in metric_rows(result, config.evaluation)
            if row["task"] in {"new", "repeat", "all"} and "window_days" not in row
        )
    frame = pd.DataFrame(rows)
    order = [
        "condition",
        "parameters",
        "control",
        "task",
        "window_days",
        "day_weight",
        "metric",
        "k",
    ]
    frame = frame[[*order, "mean", *[key for key in frame if key not in {*order, "mean"}]]]
    return frame


def publication_tables(config, finished, output):
    import numpy as np
    import pandas as pd

    from backend.infrastructure.research.als_report import write_csv
    from backend.infrastructure.research.complementarity import paired_score_differences
    from backend.infrastructure.research.metrics import metric_rows

    rows, sources = [], {}
    for name, trial, result, source in finished:
        metadata = dict(condition=name, **trial.parameters)
        rows.extend(dict(**metadata, **row) for row in metric_rows(result))
        sources[name] = dict(
            scores=trial.output / "scores.parquet", population=result["population"]
        )
    frame = pd.DataFrame(rows)
    leading = ["condition", *config.parameters]
    frame[[*leading, *[c for c in frame if c not in leading]]].to_csv(
        output / "metrics.csv", index=False
    )
    xp = np
    if config.parameters.get("use_gpu"):
        import cupy as xp
    paired = paired_score_differences(
        sources,
        list(config.evaluation.ks),
        windows=config.evaluation.window_days,
        xp=xp,
        reference=next(iter(sources)),
    )
    write_csv(
        output / "paired.csv",
        paired,
        first=("model_left", "model_right", "task", "window_days", "metric", "k", "mean"),
    )
    return frame
