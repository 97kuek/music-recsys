"""Analyze existing BPR epoch endpoints without fitting models or opening Test."""

import argparse
import json
import math
import re
import shlex
import time
from dataclasses import asdict, replace
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.models.retention import inspect_model_evidence
from src.infrastructure.research.bpr_audit import (
    audit_ranking,
    paired_changes,
    population_audit,
    revision_audit,
)
from src.infrastructure.research.comparison import metric_rows, staged_report, verified_result
from src.infrastructure.research.grid import factor_conditions
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.notebook.bpr_epoch_views import (
    METRICS,
    analysis_report,
    best_epoch,
    dashboard,
    make_figures,
)
from src.paths import PROJECT_ROOT


def load_trials(configs, *, factors, k):
    points, curves, trials, sources, recorded, signatures = [], [], [], [], {}, []
    reference = None
    for config in configs:
        saved = json.loads((config.output / "config.json").read_text())
        state = json.loads((config.output / "result.json").read_text())
        if saved["settings"] != json.loads(json.dumps(asdict(config), default=str)):
            raise ValueError("Saved settings differ")
        if (
            config.model != "bpr"
            or len(config.grid.get("iterations", [])) != 1
            or k not in config.evaluation.ks
        ):
            raise ValueError("Expected single-epoch BPR studies with saved K")
        if tuple(config.seeds) != (42, 43, 44):
            raise ValueError("Comparison requires seeds 42/43/44")
        if file_sha256(config.validation_dataset / "manifest.json") != saved["manifest_sha256"]:
            raise ValueError("Validation input changed")
        plan = dict(factor_conditions(config))
        entries = {c["condition"]: c for c in state["conditions"]}
        if (
            state["status"] != "succeeded"
            or state["phase"] != "validation"
            or state["planned"] != state["completed"]
            or len(entries) != state["completed"]
            or entries.keys() != plan.keys()
        ):
            raise ValueError("Complete Validation studies are required")
        if configs[0].evaluation != config.evaluation:
            raise ValueError("Evaluation settings differ")
        for name, trial in plan.items():
            if trial.parameters["factors"] != factors:
                continue
            result_path = trial.output / "result.json"
            if (
                entries[name]["status"] != "succeeded"
                or file_sha256(result_path) != entries[name]["result_sha256"]
            ):
                raise ValueError("Condition result hash changed")
            result, child, identity, source = verified_result(trial, config.evaluation)
            if reference is None:
                reference = identity
            if (
                identity != reference
                or child["source_sha256"] != saved["source_sha256"]
                or child["dependency_lock_sha256"] != saved["dependency_lock_sha256"]
            ):
                raise ValueError("Input or within-study provenance differs")
            model = trial.output / "model/manifest.json"
            if file_sha256(model) != result["serving_model"]["manifest_sha256"]:
                raise ValueError("Model manifest changed")
            manifest, weight_state = inspect_model_evidence(
                model.parent, expected_manifest_sha256=result["serving_model"]["manifest_sha256"]
            )
            if (
                manifest["provenance"]["learning_sha256"]
                != child["manifest"]["files"]["learning"]["sha256"]
                or manifest["spec"]["training_ref"] != manifest["provenance"]["learning_sha256"]
            ):
                raise ValueError("Model training input differs")
            revision = manifest["provenance"]["git_revision"]
            if revision in recorded and recorded[revision] != child["source_sha256"]:
                raise ValueError("Conflicting source hash for a recorded revision")
            recorded[revision] = child["source_sha256"]
            execution = {
                key: value
                for key, value in child["execution"].items()
                if key not in {"epochs", "seed"}
            }
            parameters = {
                key: value
                for key, value in result["model_parameters"].items()
                if key != "iterations"
            }
            signatures.append((execution, parameters, child["dependency_lock_sha256"]))
            row = dict(
                epochs=trial.parameters["iterations"],
                factors=factors,
                seed=trial.seeds[0],
                training_seconds=result["efficiency"]["training_seconds"],
            )
            for metric in ["binary_ndcg", "recall"]:
                scores = [
                    s
                    for s in result["accuracy"]
                    if s["task"] == "discovery"
                    and s["metric"] == metric
                    and s["window_days"] == config.evaluation.primary_window_days
                    and s["k"] == k
                    and s["day_weight"] is None
                ]
                if len(scores) != 1 or scores[0]["mean"] is None:
                    raise ValueError("Required primary metrics are missing")
                row[metric] = scores[0]["mean"]
            points.append(row)
            curves.extend(
                dict(epochs=row["epochs"], factors=factors, seed=row["seed"], **r)
                for r in metric_rows(result, config.evaluation)
            )
            log = trial.output / "run.log"
            matches = re.findall(
                r"^BPR epoch (\d+)/(\d+); seconds=([0-9.]+);", log.read_text(), re.M
            )
            sources.append(
                dict(
                    **source,
                    epochs=row["epochs"],
                    seed=row["seed"],
                    config_sha256=file_sha256(trial.output / "config.json"),
                    model_sha256=file_sha256(model),
                    model_weights=weight_state,
                    log_sha256=file_sha256(log),
                    epoch_log_records=len(matches),
                    training_loss=None,
                    within_run_validation=None,
                )
            )
            trials.append(trial)
    if len(points) != 12 or {r["epochs"] for r in points} != {30, 100, 200, 500}:
        raise ValueError("The four requested epochs and all three seeds are required")
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise ValueError("Backend/device/precision/hyperparameters differ")
    import pandas as pd

    return (
        pd.DataFrame(points).sort_values(["epochs", "seed"]),
        pd.DataFrame(curves),
        trials,
        sources,
        recorded,
    )


def summarize(points, curves, k):
    import pandas as pd

    rows = []
    for epoch, group in points.groupby("epochs", sort=True):
        row = dict(epochs=int(epoch), factors=int(group.factors.iloc[0]), seeds=len(group))
        for column in ["binary_ndcg", "recall", "training_seconds"]:
            row[column + "_mean"] = float(group[column].mean())
            row[column + "_std"] = float(group[column].std(ddof=1))
        for (task, metric), (short, _) in METRICS.items():
            if task == "discovery":
                continue
            values = curves[
                (curves.epochs == epoch)
                & (curves.task == task)
                & (curves.metric == metric)
                & (curves.k == k)
            ]["mean"]
            row[short + "_mean"] = values.mean()
            row[short + "_std"] = values.std(ddof=1)
        for metric in [
            "latency_mean",
            "evaluation_seconds",
            "ranking_seconds",
            "input_tokens",
            "output_tokens",
            "api_cost",
            "llm_calls",
            "tool_calls",
        ]:
            values = curves[
                (curves.epochs == epoch) & (curves.task == "all") & (curves.metric == metric)
            ]["mean"]
            row[metric + "_mean"] = values.mean()
            row[metric + "_std"] = values.std(ddof=1)
        rows.append(row)
    return pd.DataFrame(rows)


def baselines(config, *, k, population):
    import pandas as pd

    rows = []
    reference = json.loads((config.output / "f128-s42/config.json").read_text())["manifest"]
    for name, path in [
        ("Popularity/plays", "configs/popularity-plays.json"),
        ("Popularity/users", "configs/popularity-users.json"),
    ]:
        trial = load_study_config(PROJECT_ROOT / path)
        result, saved, _, source = verified_result(trial, config.evaluation)
        if saved["manifest"] != reference or result["population"]["users"] != population["users"]:
            raise ValueError("Baseline input differs")
        row = dict(model=name, kind="measured deterministic baseline")
        for metric in ["binary_ndcg", "recall"]:
            score = [
                s
                for s in result["accuracy"]
                if s["task"] == "discovery"
                and s["metric"] == metric
                and s["k"] == k
                and s["window_days"] == config.evaluation.primary_window_days
            ]
            row[metric] = score[0]["mean"]
        rows.append(dict(**row, source=source["result"], result_sha256=source["result_sha256"]))
    # This generated config is explicitly referenced by the existing ItemCF report.
    trial = load_study_config(PROJECT_ROOT / "configs/itemcf/cosine/20.json")
    result, saved, _, source = verified_result(trial, config.evaluation)
    if saved["manifest"] != reference:
        raise ValueError("ItemCF input differs")
    row = dict(
        model="ItemCF/Cosine20",
        kind="measured deterministic baseline",
        source=source["result"],
        result_sha256=source["result_sha256"],
    )
    for metric in ["binary_ndcg", "recall"]:
        row[metric] = next(
            s["mean"]
            for s in result["accuracy"]
            if s["task"] == "discovery"
            and s["metric"] == metric
            and s["k"] == k
            and s["window_days"] == config.evaluation.primary_window_days
        )
    rows.append(row)
    rows.append(
        dict(
            model="Random (analytic expectation)",
            kind="analytic expectation; not measured",
            binary_ndcg=population["random_ndcg"],
            recall=population["random_recall"],
            source=None,
            result_sha256=None,
        )
    )
    return pd.DataFrame(rows)


def analyze(config_paths, output, *, factors=128, k=20):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from src.infrastructure.provenance import source_hash

    started = time.perf_counter()
    configs = sorted(
        [load_study_config(p) for p in config_paths], key=lambda c: c.grid["iterations"][0]
    )
    output = Path(output).resolve()
    if factors != 128 or k != 20 or configs[0].evaluation.primary_window_days != 90:
        raise ValueError(
            "This report implements the requested 128-factor / 90-day / K20 comparison"
        )
    if any(output == c.output or output.is_relative_to(c.output) for c in configs):
        raise ValueError("Analysis must not replace a source experiment")
    with study_lock(replace(configs[0], output=output)):
        if (output / "result.json").exists() or (output / "config.json").exists():
            raise FileExistsError("Saved analysis is preserved; choose a new output")
        points, curves, trials, sources, recorded = load_trials(configs, factors=factors, k=k)
        audit = revision_audit(PROJECT_ROOT, recorded)
        connection, manifest, population = population_audit(
            configs[0].validation_dataset, window_days=90, k=k
        )
        try:
            ranking_audits = []
            for trial in trials:
                checked = audit_ranking(connection, trial, items=population["items"], k=k)
                point = points[
                    (points.epochs == trial.parameters["iterations"])
                    & (points.seed == trial.seeds[0])
                ].iloc[0]
                if not all(
                    math.isclose(checked[m], point[m], rel_tol=1e-10, abs_tol=1e-12)
                    for m in ["binary_ndcg", "recall"]
                ):
                    raise ValueError("Recomputed aggregate metrics differ")
                ranking_audits.append(
                    dict(epochs=int(point.epochs), seed=int(point.seed), **checked)
                )
                print(
                    f"Verified saved rankings: {int(point.epochs)} epochs / seed {int(point.seed)}",
                    flush=True,
                )
        finally:
            connection.close()
        paired_inputs = {
            epoch: [
                str(t.output / "scores.parquet")
                for t in trials
                if t.parameters["iterations"] == epoch
            ]
            for epoch in [30, 100, 200, 500]
        }
        paired = paired_changes(
            paired_inputs, window_days=90, k_index=configs[0].evaluation.ks.index(k) + 1
        )
        baseline = baselines(configs[0], k=k, population=population)
        summary = summarize(points, curves, k)
        images = make_figures(points, curves, summary, factors=factors, window_days=90, k=k)
        command = shlex.join(
            [".venv/bin/python", "-m", "src.interfaces.notebook.bpr_epochs", "--configs"]
            + [str(c.path.relative_to(PROJECT_ROOT)) for c in configs]
            + ["--factors", "128", "--k", "20", "--output", str(output)]
        )
        report = analysis_report(
            summary, points, paired, baseline, population, audit, command=command
        )
        if len(report.splitlines()) > 200:
            raise ValueError("Analysis report exceeds 200 lines")
        try:
            with staged_report(output) as stage:
                summary.to_csv(stage / "metrics.csv", index=False)
                points.to_csv(stage / "conditions.csv", index=False)
                curves.to_csv(stage / "sensitivity.csv", index=False)
                pd.DataFrame(paired).to_csv(stage / "paired.csv", index=False)
                baseline.to_csv(stage / "baselines.csv", index=False)
                for name, fig in images.items():
                    fig.savefig(stage / name, dpi=180, facecolor="white")
                (stage / "dashboard.html").write_text(
                    dashboard(
                        summary, baseline, images, population, factors=factors, window_days=90, k=k
                    ),
                    encoding="utf-8",
                )
                (stage / "analysis.md").write_text(report, encoding="utf-8")
                atomic_json(
                    stage / "config.json",
                    dict(
                        format="music-recsys-bpr-epochs-config-v1",
                        source_configs=[str(c.path) for c in configs],
                        factors=factors,
                        epochs=[30, 100, 200, 500],
                        seeds=[42, 43, 44],
                        window_days=90,
                        task="discovery",
                        k=k,
                        primary_metric="binary_ndcg",
                        secondary_metric="recall",
                        manifest_sha256=file_sha256(
                            configs[0].validation_dataset / "manifest.json"
                        ),
                        render_source_sha256=source_hash(),
                        command=command,
                    ),
                )
                (stage / "run.log").write_text(
                    "SUCCEEDED: reused 12 saved Validation conditions; source/model/ranking audits passed; no model fitting or Test access\nTraining Loss and within-run Validation curve are unrecorded\n"
                )
                artifacts = {p.name: file_sha256(p) for p in stage.iterdir() if p.is_file()}
                atomic_json(
                    stage / "result.json",
                    dict(
                        format="music-recsys-bpr-epochs-analysis-v1",
                        status="succeeded",
                        phase="validation",
                        source_conditions=sources,
                        source_audit=audit,
                        population=population,
                        ranking_audits=ranking_audits,
                        training_loss=None,
                        within_run_validation=None,
                        recommendation=dict(
                            factors=128,
                            epochs=best_epoch(summary),
                            basis="highest observed mean Validation Binary NDCG@20 at fixed factors=128",
                            cost_alternative_epochs=200,
                            scope="conditional on factors=128; no freeze or Test",
                        ),
                        figures=list(images),
                        artifacts=artifacts,
                        analysis_seconds=time.perf_counter() - started,
                    ),
                )
        finally:
            for fig in images.values():
                plt.close(fig)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--factors", type=int, default=128)
    parser.add_argument("--k", type=int, default=20)
    args = parser.parse_args()
    print(analyze(args.configs, args.output, factors=args.factors, k=args.k))


if __name__ == "__main__":
    main()
