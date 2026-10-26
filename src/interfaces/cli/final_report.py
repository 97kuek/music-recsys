"""Compare frozen Test conditions, keeping seed variation separate from user intervals."""

import json
from collections import defaultdict
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.comparison import staged_report
from src.infrastructure.research.full_test import restore, verify_test
from src.infrastructure.research.metrics import average_rows
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.fixed_test import _reference, _verify_references
from src.interfaces.cli.sensitivity import condition_name


def summarize(output, protocol):
    import numpy as xp

    if any((output / name).exists() for name in ("result.json", "metrics.csv", "paired.csv")):
        raise FileExistsError("Completed or partial Test comparison is preserved")

    from src.infrastructure.provenance import source_hash
    from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu
    from src.infrastructure.research.als_report import write_csv
    from src.infrastructure.research.complementarity import paired_score_differences
    from src.interfaces.notebook.model_figures import save_figures
    from src.paths import PROJECT_ROOT

    if (
        source_hash() != protocol["source_sha256"]
        or file_sha256(PROJECT_ROOT / "uv.lock") != protocol["dependency_lock_sha256"]
    ):
        raise ValueError("Frozen Test processing source or lock changed")
    _verify_references([e["source"] for e in protocol["conditions"]] + protocol["baselines"])
    grouped, sources = defaultdict(list), []
    for entry in protocol["conditions"]:
        config = restore(Path(entry["source"]["config"]).parent)
        result = verify_test(config, protocol)
        label = condition_name(config.model, config.parameters, config.seeds[0])
        if config.model == "bpr":
            label = label.rsplit("-s", 1)[0]
        name = f"{config.model}-{label}"
        source = _reference(entry["condition"], config.output / "test", result)
        sources.append(source)
        grouped[name].append((config, result, source))
    rows, evidence = [], {}
    primary_names = list(grouped)
    for name, records in grouped.items():
        configs = [r[0] for r in records]
        seeds = [c.seeds[0] for c in configs]
        if len(set(seeds)) != len(seeds) or (
            protocol["model"] == "bpr" and set(seeds) != {42, 43, 44}
        ):
            raise ValueError("Fixed Test seed set differs")
        metadata = dict(condition=name, model=protocol["model"], **configs[0].parameters)
        rows.extend(average_rows([r[1] for r in records], metadata))
        evidence[name] = dict(
            scores=[c.output / "test/scores.parquet" for c in configs],
            population=protocol["common"]["population"],
        )
    for source in protocol["baselines"]:
        directory = Path(source["result"]).parent
        result = json.loads(Path(source["result"]).read_text(encoding="utf-8"))
        verify_artifacts(directory, result["artifacts"])
        rows.extend(
            average_rows([result], dict(condition=source["condition"], model=result["model"]))
        )
        evidence[source["condition"]] = dict(
            scores=directory / "scores.parquet", population=protocol["common"]["population"]
        )
    if protocol["gpu"]:
        import cupy as xp

        print("Test comparison GPU preflight: " + require_idle_gpu(), flush=True)
    estimates = []
    differences = paired_score_differences(
        evidence,
        list(next(iter(grouped.values()))[0][0].evaluation.ks),
        windows=(30, 90, 180),
        xp=xp,
        ci_ks=(20,),
        estimates=estimates,
    )
    lookup = {
        (r["condition"], r["task"], r["window_days"], r["metric"], r["k"]): r for r in estimates
    }
    for row in rows:
        if row["aspect"] != "accuracy":
            continue
        task = "new" if row["task"] == "discovery" else row["task"]
        key = row["condition"], task, row["window_days"], row["metric"], row["k"]
        if key in lookup and row.get("day_weight") in (None, 1.0):
            for field in ("ci_low", "ci_high"):
                row[field] = lookup[key][field]
    with staged_report(output) as temporary:
        write_csv(
            temporary / "metrics.csv",
            rows,
            first=(
                "condition",
                "model",
                "factors",
                "iterations",
                "regularization",
                "neighbors",
                "k1",
                "b",
                "use_idf",
                "aggregation",
                "aspect",
                "task",
                "window_days",
                "day_weight",
                "metric",
                "k",
                "mean",
            ),
        )
        write_csv(
            temporary / "paired.csv",
            differences,
            first=("model_left", "model_right", "task", "window_days", "metric", "k", "mean"),
        )
        figures = save_figures(temporary, rows, primary_names, protocol["model"])
        _verify_references(
            sources + protocol["baselines"] + [e["source"] for e in protocol["conditions"]]
        )
        if source_hash() != protocol["source_sha256"]:
            raise ValueError("Source changed while publishing final Test")
        atomic_json(
            temporary / "result.json",
            dict(
                format="music-recsys-remaining-test-v1",
                status="succeeded",
                phase="test",
                model=protocol["model"],
                population=protocol["common"]["population"],
                selection="not_selected",
                sources=sources,
                baselines=protocol["baselines"],
                groups=primary_names,
                source_sha256=protocol["source_sha256"],
                dependency_lock_sha256=protocol["dependency_lock_sha256"],
                protocol_sha256=file_sha256(output / "config.json"),
                figures=figures,
                paired_differences=[
                    r for r in differences if r["window_days"] == 90 and r["k"] == 20
                ],
                artifacts={
                    n: dict(path=n + ".csv", sha256=file_sha256(temporary / (n + ".csv")))
                    for n in ("metrics", "paired")
                },
                definitions=dict(
                    difference="right minus left; per-user average of fixed seeds before paired user bootstrap",
                    uncertainty=protocol["uncertainty"],
                    ild="Seed means retain per-seed valid-population ranges; not a common-user ILD comparison",
                    figures="One metric per graph, 300 dpi PNG and embedded-font PDF; all-null metrics omitted",
                ),
            ),
        )
    if protocol["gpu"]:
        xp.get_default_memory_pool().free_all_blocks()
    return output
