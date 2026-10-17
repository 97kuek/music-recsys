"""共通4観点をconfig.json・result.json・metrics.csv・単独PNGへ保存する。"""

import argparse
import json
import os
import tempfile
from pathlib import Path


def build_report(config_path, output=None, *, migrate=False):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.infrastructure.research.experiment_store import atomic_json, compact, read_value
    from src.interfaces.cli.experiment_search import (
        context,
        load_study,
        load_study_config,
        study_lock,
    )
    from src.interfaces.notebook.evaluation_figures import (
        accuracy_figures,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )
    from src.interfaces.notebook.evaluation_views import (
        accuracy_groups,
        diversity_table,
        efficiency_table,
    )
    from src.interfaces.notebook.listening_analysis import curve_frame, observation_distribution
    from src.interfaces.notebook.search_analysis import validation_overview

    config = load_study_config(config_path)
    output = Path(output or config.output).resolve()
    with study_lock(config):
        if migrate:
            saved = read_value(config.output / "study.json")
            _, conditions = context(config)
            conditions["source_sha256"] = saved["conditions"]["source_sha256"]
            if conditions != saved["conditions"]:
                raise ValueError("入力・評価条件が異なります / 保存形式だけの移行はできません")
        else:
            saved = load_study(config)
        if not saved or saved["status"] != "succeeded":
            raise ValueError("Validationの成功結果が必要です")
        trials = [saved["trials"][key] for key in saved["planned"]]
        if len({str(t["parameters"]) for t in trials}) != 1:
            raise ValueError("異なるモデル設定は平均できません / 1設定の実験を指定してください")
        if any(t["status"] != "succeeded" for t in trials):
            raise ValueError("成功済み試行だけを集計できます")
        document = compact(config, output=output, migrate=migrate)
        bundles = [document["trials"][key]["curves"] for key in saved["planned"]]
        curves = curve_frame(bundles, config.model, "validation", 2000)
        curves = curves[curves.k.between(2, 20)].copy()
        groups = accuracy_groups(curves)
        beyond = diversity_table(curves)
        efficiency = efficiency_table(curves)
        observations = observation_distribution(config)
        _, _, population = validation_overview(config)
        columns = [
            "task",
            "metric",
            "window_days",
            "day_weight",
            "k",
            "mean",
            "ci_low",
            "ci_high",
            "n_evaluated",
            "n_excluded",
        ]
        frames = {
            "discovery": groups["新規発見"][columns],
            "preference": groups["継続的な嗜好"][columns],
            "diversity": beyond,
            "efficiency": efficiency,
            "observations": observations,
        }
        document["analysis"] = {
            "phase": "validation",
            "model": config.model,
            "population": population,
            "window_days": config.evaluation.primary_window_days,
            "day_weight": config.evaluation.primary_day_weight,
            "aspects": {
                name: json.loads(frame.to_json(orient="records", double_precision=15))
                for name, frame in frames.items()
            },
            "definitions": {
                "accuracy": "macro mean over users with candidate-relevant positives",
                "diversity": "all requests, regardless of future positives",
                "efficiency": "seed mean of per-run totals; latency in seconds per request; unmeasured is null",
                "graded_ndcg": "within-window preference proxy; not causal evidence of persistent preference",
            },
        }
        metrics = [curves[columns].rename(columns={"mean": "value"}).assign(aspect="accuracy")]
        metrics.append(
            beyond.melt(
                id_vars=["task", "k"],
                value_vars=["ild", "catalog_coverage", "novelty"],
                var_name="metric",
                value_name="value",
            ).assign(aspect="diversity")
        )
        metrics.append(
            efficiency.melt(id_vars=["model"], var_name="metric", value_name="value").assign(
                aspect="efficiency"
            )
        )
        metrics.append(
            observations.rename(columns={"observation": "metric", "pairs": "value"}).assign(
                aspect="observations"
            )
        )
        metrics = pd.concat(metrics, ignore_index=True)
        order = [
            "aspect",
            "task",
            "metric",
            "window_days",
            "day_weight",
            "k",
            "bin",
            "value",
            "ci_low",
            "ci_high",
            "n_evaluated",
            "n_excluded",
            "model",
        ]
        metrics = metrics.reindex(columns=order)
        with tempfile.TemporaryDirectory(prefix=".render-", dir=output.parent) as temp:
            stage = Path(temp)
            metrics.to_csv(stage / "metrics.csv", index=False, encoding="utf-8-sig")
            figures = {}
            for frame in groups.values():
                figures.update(accuracy_figures(frame))
            figures.update(diversity_figures(beyond))
            figures.update(efficiency_figures(efficiency))
            figures.update(observation_figures(observations))
            for name, figure in figures.items():
                figure.savefig(stage / f"{name}.png", dpi=160)
                plt.close(figure)
            document["analysis"]["figures"] = [f"{name}.png" for name in figures]
            for path in stage.iterdir():
                os.replace(path, output / path.name)
        from src.interfaces.notebook.evaluation_figures import prune_generated_figures

        prune_generated_figures(output, document["analysis"]["figures"])
        atomic_json(output / "result.json", document)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--migrate", action="store_true", help="保存条件を保持し、旧フォルダ構成を移行"
    )
    args = parser.parse_args()
    print(build_report(args.config, args.output, migrate=args.migrate))


if __name__ == "__main__":
    main()
