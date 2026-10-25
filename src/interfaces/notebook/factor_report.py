"""Verified comparisons and one-metric figures for full factor experiments."""

import json
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.results import read_result
from src.infrastructure.research.search import load_study_config
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.comparison import metric_rows, staged_report, verified_result


def build_report(config, paths, compare=()):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES, factor_figures

    expanded = list(paths)
    for path in compare:
        trial = load_study_config(path)
        result = read_result(trial)
        if result.get("format") == "music-recsys-grid-comparison-v1":
            for source in result["sources"]:
                if file_sha256(source["result"]) != source["result_sha256"]:
                    raise ValueError("Comparison source changed")
                expanded.append(Path(source["config"]))
        else:
            expanded.append(path)
    rows, sources, reference = [], [], None
    for path in dict.fromkeys(Path(p).resolve() for p in expanded):
        trial = load_study_config(path)
        result, saved, identity, source = verified_result(trial, config.evaluation)
        if reference is None:
            reference = identity
        if identity != reference:
            raise ValueError("Comparison input or evaluation conditions differ")
        sources.append(dict(**source, source_sha256=saved.get("source_sha256")))
        label_parameters = dict(trial.parameters)
        if trial.model == "popularity":
            label_parameters.setdefault("basis", result.get("popularity_basis", "play_count"))
        label = f"{trial.model} / " + ", ".join(
            f"{k}={v}"
            for k, v in label_parameters.items()
            if k
            in {
                "factors",
                "similarity",
                "neighbors",
                "basis",
                "iterations",
                "learning_rate",
                "regularization",
                "alpha",
                "confidence_transform",
                "use_gpu",
                "cg_steps",
            }
        )
        condition = dict(
            experiment=label,
            model=trial.model,
            factors=trial.parameters.get("factors"),
            seed=trial.seeds[0],
            parameters=json.dumps(trial.parameters, sort_keys=True),
        )
        rows.extend(dict(**condition, **score) for score in metric_rows(result, config.evaluation))
    if (config.output / "result.json").exists():
        prior = read_result(config)
        if prior["sources"] != sources:
            raise ValueError("Existing report differs; choose a new output")
        return config.output
    frame = pd.DataFrame(rows)
    # Stage figures and publish the result marker only after all artifacts are ready.
    with staged_report(config.output) as output:
        frame.to_csv(output / "metrics.csv", index=False, encoding="utf-8-sig")
        names = []
        for (task, metric), values in frame.groupby(["task", "metric"], sort=False):
            if not values["mean"].notna().any():
                continue
            name, title = ACCURACY_NAMES.get(
                (task, metric), (f"{metric}_{task}", f"{metric} · {task}")
            )
            for suffix, subset in (
                ("", values),
                ("_factors", values[values.model == config.model]),
                ("_k20", values[(values.model == config.model) & (values.k == 20)]),
            ):
                if not subset["mean"].notna().any():
                    continue
                fig, ax = plt.subplots(figsize=(16, 7))
                if task == "all" or suffix == "_k20":
                    ax.bar(range(len(subset)), subset["mean"])
                    ax.set_xticks(
                        range(len(subset)),
                        [
                            f"factors={int(f)}" if suffix == "_k20" else label
                            for f, label in zip(subset.factors, subset.experiment, strict=True)
                        ],
                    )
                    ax.tick_params(axis="x", labelrotation=15, labelsize=6)
                else:
                    for label, group in subset.groupby("experiment", sort=False):
                        group = group.sort_values("k")
                        ax.plot(group.k, group["mean"], marker="o", markersize=3, label=label)
                    ax.set(xlabel="K", xticks=sorted(subset.k.unique()))
                    ax.legend(fontsize=6, loc="upper left", bbox_to_anchor=(1.01, 1))
                ax.set(
                    ylabel=title,
                    title=f"{title}{' @20' if suffix == '_k20' else ''} · Validation {config.evaluation.primary_window_days}d",
                    ylim=(0, None),
                )
                ax.grid(alpha=0.2)
                fig.tight_layout()
                filename = f"{name}{suffix}.png"
                fig.savefig(output / filename, dpi=160)
                plt.close(fig)
                names.append(filename)
        for name, figure in factor_figures(
            frame, config.model, config.evaluation.primary_window_days
        ).items():
            figure.savefig(output / f"{name}.png", dpi=160)
            plt.close(figure)
        atomic_json(
            output / "config.json",
            dict(
                format="music-recsys-full-config-v1",
                settings=json.loads(json.dumps(asdict(config), default=str)),
                **reference,
            ),
        )
        atomic_json(
            output / "result.json",
            dict(
                format="music-recsys-grid-comparison-v1",
                status="succeeded",
                selection="not_selected",
                model=config.model,
                sources=sources,
                population=reference["population"],
                figures=names,
                metrics=json.loads(frame.to_json(orient="records", double_precision=15)),
            ),
        )
        (output / "run.log").write_text(
            "SUCCEEDED: verified Validation comparisons; no setting selected\n"
        )
        # study_lock creates the output directory, which must contain no report yet.
    return config.output
