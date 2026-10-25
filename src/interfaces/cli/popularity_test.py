"""Run the two predeclared Popularity baselines once on the full final Test."""

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, read_bundle
from src.infrastructure.research.results import read_result, verify_artifacts
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.catalog import metric_rows


def run(config_paths, *, gpu=True):
    configs = [load_study_config(p) for p in config_paths]
    if len(configs) != 2 or any(c.model != "popularity" or c.grid for c in configs):
        raise ValueError("Exactly two fixed Popularity configurations are required")
    if [c.parameters for c in configs] != [{}, {"basis": "user_count"}]:
        raise ValueError("Pass play-count then user-count Popularity configurations")
    if len({c.output.parent for c in configs}) != 1:
        raise ValueError("Both conditions must share a method directory")
    if any(
        c.evaluation.primary_window_days != 90
        or c.evaluation.primary_day_weight != 1.0
        or 20 not in c.evaluation.ks
        for c in configs
    ):
        raise ValueError("This final report requires the declared 90d/weight1/K20 display")
    output = configs[0].output.parent / "test"
    if output.exists() or any((c.output / "test").exists() for c in configs):
        raise FileExistsError("Final Test outputs already exist; never overwrite or retune")
    common, sources = None, []
    for c in configs:
        result = read_result(c)
        if result["status"] != "succeeded" or result["phase"] != "validation":
            raise ValueError("Both Validation conditions must have succeeded")
        verify_artifacts(c.output, result["artifacts"])
        manifest, _ = read_bundle(c.test_dataset)
        signature = dict(
            manifest=manifest,
            evaluation=asdict(c.evaluation),
            features_sha256=file_sha256(c.features),
        )
        if common is not None and common != signature:
            raise ValueError("Test input, evaluation or features differ")
        common = signature
        sources.append(
            dict(
                condition=c.output.name,
                parameters=c.parameters,
                config=str(c.path),
                config_sha256=file_sha256(c.path),
                validation_result=str(c.output / "result.json"),
                validation_result_sha256=file_sha256(c.output / "result.json"),
            )
        )
    output.mkdir()
    from dataclasses import replace

    with study_lock(replace(configs[0], output=output)):
        from src.infrastructure.provenance import source_hash
        from src.paths import PROJECT_ROOT

        protocol = dict(
            format="music-recsys-popularity-final-test-config-v1",
            phase="test",
            common=common,
            conditions=sources,
            gpu=gpu,
            report=dict(window_days=90, day_weight=1.0, k=20),
            source_sha256=source_hash(),
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            policy="Both baselines fixed before Test scoring; no selection or retuning from Test",
        )
        atomic_json(output / "config.json", protocol)
        with (output / "run.log").open("x") as log:
            log.write("FROZEN: play-count and user-count; full population; all four aspects\n")
            log.flush()
            from src.interfaces.cli.evaluate import run as evaluate

            results = []
            for c in configs:
                path = evaluate(c, phase="test", gpu_popularity=gpu)
                results.append(json.loads((path / "result.json").read_text(encoding="utf-8")))
                log.write(f"SUCCEEDED {path}\n")
                log.flush()
            _summarize(configs, results, output, sources, gpu=gpu)
            log.write("SUCCEEDED: both final Test conditions and paired comparison\n")
    return output


def _summarize(configs, results, output, sources, *, gpu):
    from src.infrastructure.provenance import source_hash

    if results[0]["population"] != results[1]["population"]:
        raise ValueError("Test populations differ")
    import numpy as np

    from src.infrastructure.research.complementarity import paired_score_differences

    xp = np
    if gpu:
        import cupy as cp

        xp = cp
    evidence = {
        c.output.name: dict(scores=c.output / "test/scores.parquet", population=r["population"])
        for c, r in zip(configs, results, strict=True)
    }
    differences = paired_score_differences(
        evidence,
        list(configs[0].evaluation.ks),
        windows=configs[0].evaluation.window_days,
        xp=xp,
        ci_ks=(20,),
    )
    rows, references = [], []
    for c, r in zip(configs, results, strict=True):
        path = c.output / "test"
        verify_artifacts(path, r["artifacts"])
        rows.extend(
            dict(condition=c.output.name, phase="test", **row)
            for row in metric_rows(r)
            if "phase" not in row
        )
        rows.extend(
            dict(condition=c.output.name, **row) for row in metric_rows(r) if "phase" in row
        )
        references.append(
            dict(
                condition=c.output.name,
                result=str(path / "result.json"),
                result_sha256=file_sha256(path / "result.json"),
                config=str(path / "config.json"),
                config_sha256=file_sha256(path / "config.json"),
            )
        )
    rows.extend(
        dict(condition="users-minus-plays", phase="test", aspect="paired", **r) for r in differences
    )
    columns = [
        "condition",
        "phase",
        "aspect",
        "task",
        "window_days",
        "day_weight",
        "metric",
        "k",
        "mean",
    ]
    columns.extend(sorted({k for row in rows for k in row} - set(columns)))
    with (output / "metrics.csv").open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    figures = plot_results(output, configs, results, differences)
    for source in sources:
        for name in ("config", "validation_result"):
            if file_sha256(source[name]) != source[name + "_sha256"]:
                raise ValueError("Frozen source changed during Test")
    atomic_json(
        output / "result.json",
        dict(
            format="music-recsys-popularity-final-test-v1",
            status="succeeded",
            phase="test",
            population=results[0]["population"],
            conditions=references,
            protocol_sha256=file_sha256(output / "config.json"),
            aggregation_source_sha256=source_hash(),
            paired_differences=differences,
            definitions=dict(
                difference="users minus plays, matched positive users",
                ci="95% user bootstrap, 2000 resamples, seed42; 90d/K20/dayweight1 only; no multiplicity correction",
                interpretation="Final baseline comparison; no model selected or retuned using Test",
            ),
            artifacts=dict(
                metrics=dict(path="metrics.csv", sha256=file_sha256(output / "metrics.csv"))
            ),
            figures=figures,
        ),
    )
    return output


def finish(config_paths):
    """Publish a missing aggregate from successful Test artifacts without rescoring."""
    from dataclasses import replace

    configs = [load_study_config(p) for p in config_paths]
    output = configs[0].output.parent / "test"
    if (output / "result.json").exists() or (output / "metrics.csv").exists():
        raise FileExistsError("Preserve existing aggregate or partial CSV before finalizing")
    protocol = json.loads((output / "config.json").read_text(encoding="utf-8"))
    if len(configs) != 2 or [c.output.name for c in configs] != [
        s["condition"] for s in protocol["conditions"]
    ]:
        raise ValueError("Frozen conditions differ")
    results = []
    with study_lock(replace(configs[0], output=output)):
        for c in configs:
            path = c.output / "test"
            saved = json.loads((path / "config.json").read_text(encoding="utf-8"))
            result = json.loads((path / "result.json").read_text(encoding="utf-8"))
            settings = json.loads(json.dumps(asdict(replace(c, output=path)), default=str))
            manifest, _ = read_bundle(c.test_dataset)
            if (
                result["status"] != "succeeded"
                or result["phase"] != "test"
                or saved["settings"] != settings
                or saved["manifest"] != manifest
                or manifest != protocol["common"]["manifest"]
                or saved["source_sha256"] != protocol["source_sha256"]
                or saved["dependency_lock_sha256"] != protocol["dependency_lock_sha256"]
                or saved["features_sha256"] != file_sha256(c.features)
                or saved["features_sha256"] != protocol["common"]["features_sha256"]
            ):
                raise ValueError("Successful Test evidence differs from the frozen protocol")
            verify_artifacts(path, result["artifacts"])
            results.append(result)
        if protocol["gpu"]:
            from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

            require_idle_gpu()
        result = _summarize(configs, results, output, protocol["conditions"], gpu=protocol["gpu"])
        with (output / "run.log").open("a", encoding="utf-8") as log:
            log.write(
                "SUCCEEDED: aggregate finalized from verified saved Test artifacts; no rescoring\n"
            )
        return result


def plot_results(output, configs, results, differences=()):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.popularity_figures import comparison_figures

    figures = comparison_figures(configs, results, differences)
    for name, fig in figures.items():
        fig.savefig(output / f"{name}.png", dpi=300)
        plt.close(fig)
    return [f"{name}.png" for name in figures]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", type=Path, nargs=2, required=True)
    parser.add_argument("--cpu", action="store_true", help="Explicit CPU reference execution")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--summarize", action="store_true", help="Only finish the saved Test aggregate"
    )
    actions.add_argument(
        "--replot", action="store_true", help="Redraw saved Test figures; never score or retune"
    )
    args = parser.parse_args()
    if args.replot:
        from src.interfaces.cli.popularity_replot import replot

        print(replot(args.configs))
    else:
        print(finish(args.configs) if args.summarize else run(args.configs, gpu=not args.cpu))


if __name__ == "__main__":
    main()
