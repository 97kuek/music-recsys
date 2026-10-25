"""Popularity Test figures from saved estimates, without scoring or selection."""


def comparison_figures(configs, results, differences=()):
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import (
        accuracy_figures,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )

    accuracy, diversity, efficiency = [], [], []
    for config, result in zip(configs, results, strict=True):
        name = config.output.name
        accuracy.extend(dict(r, experiment=name) for r in result["accuracy"])
        diversity.extend(dict(r, experiment=name) for r in result["diversity"])
        efficiency.append(dict(result["efficiency"], experiment=name))
    frame = pd.DataFrame(accuracy)
    primary = frame[
        (frame.window_days == 90) & ((frame.metric != "graded_ndcg") | (frame.day_weight == 1.0))
    ]
    names = {
        "binary_ndcg": "binary_ndcg_new",
        "recall": "recall_new",
        "repeat_recall": "repeat_recall_repeat",
    }
    figures = {names.get(k, k): v for k, v in accuracy_figures(primary).items()}
    figures.update(diversity_figures(pd.DataFrame(diversity), separate_tasks=True))
    figures.update(efficiency_figures(pd.DataFrame(efficiency)))
    if results[0]["observations"] != results[1]["observations"]:
        raise ValueError("The two Test observation distributions differ")
    figures.update(observation_figures(pd.DataFrame(results[0]["observations"])))
    figures.update(paired_difference_figures(differences))
    return figures


def paired_difference_figures(differences):
    """One endpoint/metric per figure; no joint axis for unlike units."""
    from src.interfaces.notebook.scientific_plots import STYLES, axes

    names = {
        ("new", "paired_difference_binary_ndcg"): ("binary_ndcg_new", "New-track binary NDCG", 1),
        ("new", "paired_difference_recall"): ("recall_new", "New-track recall", 100),
        ("repeat", "paired_difference_graded_ndcg"): (
            "graded_ndcg_repeat",
            "Previously heard-track graded NDCG",
            1,
        ),
        ("repeat", "paired_difference_repeat_recall"): (
            "repeat_recall_repeat",
            "Repeat recall",
            100,
        ),
    }
    figures = {}
    for row in differences:
        spec = names.get((row["task"], row["metric"]))
        if (
            spec is None
            or row["window_days"] != 90
            or row["k"] != 20
            or any(row.get(k) is None for k in ("mean", "ci_low", "ci_high"))
        ):
            continue
        name, metric, scale = spec
        mean, low, high = (row[k] * scale for k in ("mean", "ci_low", "ci_high"))
        fig, ax = axes(figsize=(6.4, 2.8))
        ax.grid(False)
        ax.axvline(0, color="0.4", linestyle="--", linewidth=0.8)
        ax.errorbar(
            mean,
            0,
            xerr=[[mean - low], [high - mean]],
            fmt="o",
            color=STYLES[0]["color"],
            capsize=4,
            label="Paired mean difference (95% CI)",
        )
        bound = max(abs(low), abs(high), abs(mean)) * 1.25 or 1.0
        unit = " (percentage points)" if scale == 100 else ""
        ax.set(
            xlabel=f"Difference in {metric.lower()}@20{unit}",
            yticks=[0],
            yticklabels=["Unique listener count\n− total play count"],
            xlim=(-bound, bound),
            ylim=(-0.7, 0.7),
        )
        ax.legend(frameon=False, loc="upper center")
        figures[f"delta_{name}"] = fig
    return figures


def condition_figures(document):
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import (
        accuracy_figures,
        accuracy_heatmaps,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )

    frame = pd.DataFrame(document["accuracy"])
    window = document["primary"]["primary_window_days"]
    weight = document["primary"]["primary_day_weight"]
    primary = frame[
        (frame.window_days == window)
        & ((frame.metric != "graded_ndcg") | (frame.day_weight == weight))
    ]
    figures = accuracy_figures(primary)
    figures.update(diversity_figures(pd.DataFrame(document["diversity"])))
    figures.update(efficiency_figures(pd.DataFrame([document["efficiency"]])))
    figures.update(observation_figures(pd.DataFrame(document["observations"])))
    figures.update(accuracy_heatmaps(frame, weight))
    return figures
