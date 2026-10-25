"""Evaluate predeclared full-population conditions; compare without selecting a winner."""

import argparse
import csv
import json
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, read_bundle
from src.infrastructure.research.grid import grid_variants as variants
from src.infrastructure.research.results import read_result, verify_artifacts
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.paths import PROJECT_ROOT


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(config, result, saved):
    return dict(
        manifest=saved["manifest"],
        features_sha256=saved["features_sha256"],
        population=result["population"],
        evaluation=json.loads(json.dumps(asdict(config.evaluation))),
    )


def _plan(config_path, *, extra=()):
    parent = load_study_config(config_path)
    if parent.model not in {"itemcf", "als"}:
        raise ValueError("Pass an existing ItemCF or ALS grid")
    if parent.model == "itemcf" and (parent.parameters or extra):
        raise ValueError("Pass the existing ItemCF similarity/neighbors grid")
    if (
        parent.evaluation.primary_window_days != 90
        or parent.evaluation.primary_day_weight != 1.0
        or 20 not in parent.evaluation.ks
    ):
        raise ValueError("The report requires 90d/weight1/K20; evaluation conditions stay fixed")
    configs = [load_study_config(p) for p, _ in variants(parent)]
    configs.extend(load_study_config(p) for p in extra)
    if len({c.output for c in configs}) != len(configs):
        raise ValueError("Repeated Test output")
    if any(not c.output.is_relative_to(parent.output) for c in configs):
        raise ValueError("All conditions must belong to the same method output")
    if parent.model == "als":
        if len({c.parameters.get("factors") for c in configs}) != len(configs):
            raise ValueError("Factor conditions must be distinct")
        reference = None
        for c in configs:
            if c.model != "als" or c.grid or len(c.seeds) != 1:
                raise ValueError("ALS Test requires fixed ALS conditions")
            params = {
                k: v for k, v in c.parameters.items() if k not in {"factors", "use_gpu", "cg_steps"}
            }
            signature = params, c.seeds, c.parameters.get("cg_steps", 20), c.evaluation
            if reference is not None and signature != reference:
                raise ValueError("Factor comparison requires identical ALS settings and seed")
            reference = signature
    return parent, configs


def _baseline_sources(parent, common):
    """Reuse matching successful Popularity Tests, if present; never evaluate them here."""
    baselines = []
    candidates = [
        (f"popularity-{name}", "popularity", parent.output.parent / "popularity" / name / "test")
        for name in ("plays", "users")
    ]
    if parent.model == "als":
        candidates.extend(
            (f"itemcf-{s}-{n}", "itemcf", parent.output.parent / "itemcf" / s / str(n) / "test")
            for s in ("cosine", "jaccard")
            for n in (20, 50, 100)
        )
    for name, model, directory in candidates:
        if not (directory / "result.json").exists():
            continue
        saved, result = _read(directory / "config.json"), _read(directory / "result.json")
        identity = dict(
            manifest=saved["manifest"],
            evaluation=saved["settings"]["evaluation"],
            features_sha256=saved["features_sha256"],
        )
        if (
            result["phase"] != "test"
            or result["status"] != "succeeded"
            or identity != common
            or result["model"] != model
        ):
            raise ValueError("Saved baseline Test protocol differs")
        verify_artifacts(directory, result["artifacts"])
        baselines.append(_reference(name, directory, result))
    return baselines


def _reference(name, directory, result):
    return dict(
        condition=name,
        config=str(directory / "config.json"),
        config_sha256=file_sha256(directory / "config.json"),
        result=str(directory / "result.json"),
        result_sha256=file_sha256(directory / "result.json"),
        population=result["population"],
    )


def _verify_references(references):
    for reference in references:
        for role in ("config", "result"):
            if file_sha256(reference[role]) != reference[role + "_sha256"]:
                raise ValueError("Frozen evidence changed during Test")


def run(config_path, *, gpu=True, extra=()):
    from src.infrastructure.provenance import source_hash
    from src.infrastructure.research.configuration import load_settings

    parent, configs = _plan(config_path, extra=extra)
    output = parent.output / "test"
    if output.exists() or any((c.output / "test").exists() for c in configs):
        raise FileExistsError("Saved or partial Test outputs exist; never overwrite or retune")
    common, sources = None, []
    for path, planned in variants(parent):
        if load_settings(path) != planned:
            raise ValueError("Saved condition differs from the predeclared grid")
    for config in configs:
        result = read_result(config)
        if result["status"] != "succeeded" or result["phase"] != "validation":
            raise ValueError("Every Validation condition must have succeeded")
        verify_artifacts(config.output, result["artifacts"])
        manifest, _ = read_bundle(config.test_dataset)
        signature = dict(
            manifest=manifest,
            evaluation=json.loads(json.dumps(asdict(config.evaluation))),
            features_sha256=file_sha256(config.features),
        )
        if common is not None and signature != common:
            raise ValueError("Test conditions differ")
        common = signature
        sources.append(_reference(_name(config), config.output, result))
    baselines = _baseline_sources(parent, common)
    if gpu:
        from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

        require_idle_gpu()
    protocol = dict(
        format=f"music-recsys-{parent.model}-final-test-config-v1",
        phase="test",
        common=common,
        conditions=[
            dict(source=s, parameters=c.parameters, path=str(c.path))
            for c, s in zip(configs, sources, strict=True)
        ],
        baselines=baselines,
        gpu=gpu,
        report=dict(window_days=90, day_weight=1.0, k=20),
        source_sha256=source_hash(),
        dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
        comparisons="All declared pairs, all K; paired 95% user-bootstrap intervals at 90d/K20/weight1; no multiplicity correction",
        policy="All conditions fixed before scoring; no selection or retuning using Test",
        execution=dict(als_backend="cuda" if gpu else "cpu") if parent.model == "als" else {},
    )
    output.mkdir()
    with ExitStack() as stack:
        stack.enter_context(study_lock(replace(parent, output=output)))
        atomic_json(output / "config.json", protocol)
        cache = None
        if parent.model == "itemcf":
            _, paths = read_bundle(parent.test_dataset)
            cache = (
                parent.validation_dataset.parents[2]
                / "processed/itemcf"
                / file_sha256(paths["learning"])
            )
            stack.enter_context(study_lock(replace(parent, output=cache)))
        with (output / "run.log").open("x", encoding="utf-8") as log:
            log.write("FROZEN: all predeclared conditions; full population; all four aspects\n")
            log.flush()
            from src.interfaces.cli.evaluate import run as evaluate

            for config in configs:
                directory = evaluate(
                    config,
                    model_cache=cache,
                    phase="test",
                    gpu_itemcf=gpu and parent.model == "itemcf",
                    als_backend=("cuda" if gpu else "cpu") if parent.model == "als" else None,
                )
                log.write(f"SUCCEEDED {directory}\n")
                log.flush()
            summarize(parent, configs, output, protocol)
            log.write("SUCCEEDED: verified final Test and fixed comparisons\n")
    return output


def _name(config):
    if config.model == "als":
        return f"als-{config.parameters['factors']}"
    return f"{config.parameters['similarity']}-{config.parameters['neighbors']}"


def summarize(parent, configs, output, protocol):
    import numpy as np

    from src.infrastructure.provenance import source_hash
    from src.infrastructure.research.complementarity import paired_score_differences
    from src.interfaces.cli.catalog import metric_rows

    if parent.model == "als":
        from src.interfaces.notebook.als_test_figures import save_figures
    else:
        from src.interfaces.notebook.itemcf_test_figures import save_figures

    aggregate_source = source_hash()
    if file_sha256(PROJECT_ROOT / "uv.lock") != protocol["dependency_lock_sha256"]:
        raise ValueError("Dependency lock changed after conditions were fixed")
    _verify_references([r["source"] for r in protocol["conditions"]] + protocol["baselines"])
    records, sources, rows, common = [], [], [], None
    for c in configs:
        directory = c.output / "test"
        result, saved = _read(directory / "result.json"), _read(directory / "config.json")
        if result["status"] != "succeeded" or result["phase"] != "test":
            raise ValueError("Every saved Test must have succeeded")
        expected = json.loads(json.dumps(asdict(replace(c, output=directory)), default=str))
        if saved["settings"] != expected or saved["source_sha256"] != protocol["source_sha256"]:
            raise ValueError("Saved Test differs from the frozen implementation/configuration")
        if parent.model == "als":
            actual = dict(c.parameters, use_gpu=protocol["gpu"])
            actual.setdefault("cg_steps", 20)
            if saved["execution"].get("training_parameters") != actual or saved["execution"][
                "evaluation_device"
            ] != ("cuda" if protocol["gpu"] else "cpu"):
                raise ValueError("Saved ALS Test backend differs from the frozen protocol")
        identity = _identity(c, result, saved)
        if common is not None and identity != common:
            raise ValueError("Test populations or inputs differ")
        common = identity
        if {key: identity[key] for key in protocol["common"]} != protocol["common"]:
            raise ValueError("Saved Test input differs from the frozen protocol")
        verify_artifacts(directory, result["artifacts"])
        source = _reference(_name(c), directory, result)
        sources.append(source)
        records.append(dict(condition=_name(c), **c.parameters, result=result))
    for baseline in protocol["baselines"]:
        result = _read(Path(baseline["result"]))
        if result["population"] != common["population"]:
            raise ValueError("Baseline population differs")
        verify_artifacts(Path(baseline["result"]).parent, result["artifacts"])
        records.append(dict(condition=baseline["condition"], result=result))
        sources.append(baseline)
    xp = np
    if protocol["gpu"]:
        import cupy as xp

        from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu

        require_idle_gpu()
    evidence = {
        s["condition"]: dict(
            scores=Path(s["result"]).parent / "scores.parquet", population=s["population"]
        )
        for s in sources
    }
    differences = paired_score_differences(
        evidence,
        list(parent.evaluation.ks),
        windows=parent.evaluation.window_days,
        xp=xp,
        ci_ks=(20,),
    )
    for record in records:
        condition = {k: v for k, v in record.items() if k != "result"}
        rows.extend(dict(condition, **r) for r in metric_rows(record["result"]))
    rows.extend(dict(condition="paired", aspect="paired", phase="test", **r) for r in differences)
    condition_columns = ["factors"] if parent.model == "als" else ["similarity", "neighbors"]
    columns = [
        "condition",
        *condition_columns,
        "aspect",
        "task",
        "window_days",
        "day_weight",
        "metric",
        "k",
        "mean",
    ]
    columns.extend(sorted({k for row in rows for k in row} - set(columns)))
    with (output / "metrics.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    figures = save_figures(output, records)
    _verify_references(sources + [r["source"] for r in protocol["conditions"]])
    if source_hash() != aggregate_source:
        raise ValueError("Aggregation implementation changed while publishing")
    atomic_json(
        output / "result.json",
        dict(
            format=f"music-recsys-{parent.model}-final-test-v1",
            status="succeeded",
            phase="test",
            population=common["population"],
            selection="not_selected",
            sources=sources,
            paired_differences=differences,
            protocol_sha256=file_sha256(output / "config.json"),
            evaluation_source_sha256=protocol["source_sha256"],
            aggregation_source_sha256=aggregate_source,
            artifacts=dict(
                metrics=dict(path="metrics.csv", sha256=file_sha256(output / "metrics.csv"))
            ),
            figures=figures,
            definitions=dict(
                difference="model_right minus model_left; matched positive users",
                ci=protocol["comparisons"],
            ),
        ),
    )


def finish(config_path, *, extra=()):
    parent, configs = _plan(config_path, extra=extra)
    output = parent.output / "test"
    if any((output / name).exists() for name in ("result.json", "metrics.csv")):
        raise FileExistsError("Preserve existing aggregate or partial CSV before finalizing")
    protocol = _read(output / "config.json")
    if [(str(c.path), c.parameters) for c in configs] != [
        (r["path"], r["parameters"]) for r in protocol["conditions"]
    ]:
        raise ValueError("Frozen Test conditions differ")
    with study_lock(replace(parent, output=output)):
        summarize(parent, configs, output, protocol)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write("SUCCEEDED: finalized from saved Test; no rescoring\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cpu", action="store_true", help="Explicit CPU reference evaluation")
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Finalize a missing comparison from successful saved Tests",
    )
    args = parser.parse_args()
    print(finish(args.config) if args.summarize else run(args.config, gpu=not args.cpu))


if __name__ == "__main__":
    main()
