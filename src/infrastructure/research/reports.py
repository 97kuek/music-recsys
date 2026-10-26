"""Read and verify saved comparisons, then publish tables, figures and result markers."""

import json
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.comparison import metric_rows, staged_report, verified_result
from src.infrastructure.research.grid import grid_variants
from src.infrastructure.research.results import read_result
from src.infrastructure.research.search import load_study_config
from src.infrastructure.research.store import atomic_json


class FigureWriter:
    def __init__(self, draw, *, dpi=160):
        self._draw = draw
        self._dpi = dpi

    def write(self, output, *args):
        with self._draw(*args) as figures:
            for name, figure in figures.items():
                figure.savefig(output / name, dpi=self._dpi)
            return list(figures)


class FactorReport:
    def __init__(self, draw):
        self._figures = FigureWriter(draw)

    def write(self, config, paths, compare=()):
        import pandas as pd

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
            rows.extend(
                dict(**condition, **score) for score in metric_rows(result, config.evaluation)
            )
        if (config.output / "result.json").exists():
            prior = read_result(config)
            if prior["sources"] != sources:
                raise ValueError("Existing report differs; choose a new output")
            return config.output
        frame = pd.DataFrame(rows)
        # Stage figures and publish the result marker only after all artifacts are ready.
        with staged_report(config.output) as output:
            frame.to_csv(output / "metrics.csv", index=False, encoding="utf-8-sig")
            names = self._figures.write(output, frame, config)
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
        return config.output


class ItemCFReport:
    def __init__(self, draw):
        self._figures = FigureWriter(draw)

    def write(self, config_path):
        import pandas as pd

        config = load_study_config(config_path)
        rows = []
        sources = []
        reference = None
        for path, _ in grid_variants(config):
            trial = load_study_config(path)
            similarity, neighbors = trial.parameters["similarity"], trial.parameters["neighbors"]
            result, saved, identity, source = verified_result(trial, config.evaluation)
            if reference is None:
                reference = identity
            if identity != reference:
                raise ValueError("Comparison input or evaluation conditions differ")
            sources.append(source)
            rows.extend(
                dict(similarity=similarity, neighbors=neighbors, **score)
                for score in metric_rows(
                    result,
                    config.evaluation,
                    efficiency={
                        "latency_mean",
                        "input_tokens",
                        "output_tokens",
                        "api_cost",
                        "llm_calls",
                        "tool_calls",
                    },
                )
            )
        if (config.output / "result.json").exists():
            prior = read_result(config)
            if prior["sources"] != sources:
                raise ValueError("Existing report differs; choose a new output")
            return config.output
        config.output.parent.mkdir(parents=True, exist_ok=True)
        with staged_report(config.output) as output:
            frame = pd.DataFrame(rows)
            frame.to_csv(output / "metrics.csv", index=False, encoding="utf-8-sig")
            atomic_json(
                output / "config.json",
                {
                    "format": "music-recsys-full-config-v1",
                    "settings": json.loads(json.dumps(asdict(config), default=str)),
                    "manifest": reference["manifest"],
                    "features_sha256": saved["features_sha256"],
                    "population": saved["population"],
                },
            )
            names = self._figures.write(output, frame, config)
            atomic_json(
                output / "result.json",
                dict(
                    format="music-recsys-grid-comparison-v1",
                    selection="not_selected",
                    sources=sources,
                    status="succeeded",
                    model="itemcf",
                    population=saved["population"],
                    window_days=config.evaluation.primary_window_days,
                    day_weight=config.evaluation.primary_day_weight,
                    figure_k=20,
                    figures=names,
                    metrics=json.loads(frame.to_json(orient="records", double_precision=15)),
                ),
            )
            (output / "run.log").write_text(
                "SUCCEEDED: verified Validation comparisons; no setting selected\n"
            )
        return config.output


class FullReport:
    def __init__(self, draw, accuracy_names):
        self._figures = FigureWriter(draw, dpi=300)
        self._accuracy_names = tuple(accuracy_names)

    def write(self, config, document, *, observations=None):
        import pandas as pd

        from src.infrastructure.provenance import source_hash
        from src.infrastructure.research.distributions import observation_distribution

        frame = pd.DataFrame(document["accuracy"])
        beyond = pd.DataFrame(document["diversity"])
        efficiency = pd.DataFrame([document["efficiency"]])
        if observations is None:
            observations = observation_distribution(
                config, phase=document.get("phase", "validation")
            )
        document["observations"] = json.loads(observations.to_json(orient="records"))
        document["figures"] = self._figures.write(
            config.output, config, frame, beyond, efficiency, observations
        )
        prune_figures(config.output, document["figures"], self._accuracy_names)
        document["render_source_sha256"] = source_hash()
        tables = [
            frame.rename(columns={"mean": "value"}).assign(aspect="accuracy"),
            beyond.melt(
                id_vars=["task", "k"],
                value_vars=["ild", "catalog_coverage", "novelty"],
                var_name="metric",
                value_name="value",
            ).assign(aspect="diversity"),
            efficiency.melt(id_vars=["model"], var_name="metric", value_name="value").assign(
                aspect="efficiency"
            ),
            observations.rename(columns={"observation": "metric", "pairs": "value"}).assign(
                aspect="observations"
            ),
        ]
        pd.concat(tables, ignore_index=True).to_csv(
            config.output / "metrics.csv", index=False, encoding="utf-8-sig"
        )


class StudyReport:
    def __init__(self, draw):
        self._figures = FigureWriter(draw)

    def write(self, config, finished):
        import pandas as pd

        rows = []
        for name, trial, result in finished:
            rows.extend(
                dict(
                    condition=name,
                    model=trial.model,
                    seed=trial.seeds[0],
                    factors=trial.parameters.get("factors"),
                    epochs=trial.parameters.get("iterations"),
                    neighbors=trial.parameters.get("neighbors"),
                    alpha=trial.parameters.get("alpha"),
                    regularization=trial.parameters.get("regularization"),
                    **row,
                )
                for row in metric_rows(result, config.evaluation)
            )
        frame = pd.DataFrame(rows)
        frame.to_csv(config.output / "metrics.csv", index=False)
        return self._figures.write(config.output, frame, config)


def prune_figures(output, filenames, accuracy_names):
    """共通生成図のうち今回未測定になったものを除去 / 独自の図には触れない。"""
    from pathlib import Path

    accuracy = set(accuracy_names)
    known = (
        accuracy
        | {name + "_heatmap" for name in accuracy}
        | {
            "ild",
            "coverage",
            "novelty",
            "latency",
            "input_tokens",
            "output_tokens",
            "api_cost",
            "llm_calls",
            "tool_calls",
            "play_count",
            "active_days",
        }
    )
    for name in known:
        filename = name + ".png"
        if filename not in filenames:
            (Path(output) / filename).unlink(missing_ok=True)
