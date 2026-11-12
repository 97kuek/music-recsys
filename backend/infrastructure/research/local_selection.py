"""Full-population local selection and exact replay of task-constant saved results."""

import os
import time
from collections import Counter, defaultdict
from dataclasses import asdict

from backend.application.local_selection import select_all
from backend.domain.recommendation.local_selector import comparison_policies
from backend.domain.recommendation.selection_context import SelectionContext
from backend.infrastructure.datasets.full_io import DetailWriter
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import read, reference, verify_references
from backend.infrastructure.research.results import read_result, verify_artifacts
from backend.infrastructure.research.selection_candidates import bind_candidates
from backend.infrastructure.research.selector_report import replay_pairs, replay_rows
from backend.infrastructure.research.store import atomic_json


def run(config, *, new_target, repeat_target, render):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    policies = comparison_policies(new_target, repeat_target)
    root = config.output
    output, partial = root / "selector-local", root / "selector-local.partial"
    if output.exists() or partial.exists():
        raise FileExistsError("Existing local selector output must be preserved")
    original = read_result(config)
    saved = read(root / "config.json")
    versions, candidate_refs = bind_candidates(root, saved, original)
    inputs = root / "selection-context"
    context_config, context_result = read(inputs / "config.json"), read(inputs / "result.json")
    verify_artifacts(inputs, context_result["artifacts"])
    verify_references([context_config["evidence"]])
    if (
        context_result["status"] != "succeeded"
        or context_result["phase"] != "validation"
        or context_result["users"] != original["population"]["users"]
        or context_config["candidates"] != versions
    ):
        raise ValueError("Context population or candidate versions differ")
    evidence = {**context_config["evidence"], **candidate_refs}
    evidence.update({f"context/{k}": v for k, v in reference(inputs).items()})
    for name, ref in context_result["artifacts"].items():
        evidence[f"context/{name}"] = dict(path=str(inputs / ref["path"]), sha256=ref["sha256"])
    for name, ref in original["artifacts"].items():
        evidence[f"comparison/{name}"] = dict(path=str(root / ref["path"]), sha256=ref["sha256"])
    processing = source_hash()
    partial.mkdir()
    atomic_json(
        partial / "config.json",
        dict(
            phase="validation",
            policies=[asdict(p) for p in policies],
            evidence=evidence,
            candidates=versions,
            source_sha256=processing,
            evaluation=asdict(config.evaluation),
            external_transmission=False,
            selection="not_selected",
            rule_design="exploratory_after_validation",
            execution=dict(
                backend="python",
                device="cpu",
                seed=None,
                solver="task-only dispatch; saved aggregate replay",
            ),
            policy="Abort without publication on invalid inputs; no fallback, external API or retries",
        ),
    )
    started = time.perf_counter()
    counts, totals = Counter(), defaultdict(float)
    input_seconds = 0.0

    def requests():
        nonlocal input_seconds
        previous = None
        seen = 0
        batches = iter(pq.ParquetFile(inputs / "contexts.parquet").iter_batches(batch_size=4096))
        while True:
            tick = time.perf_counter()
            batch = next(batches, None)
            if batch is None:
                break
            rows = batch.to_pylist()
            input_seconds += time.perf_counter() - tick
            for row in rows:
                user = row.pop("user_id")
                context = SelectionContext(**row)
                key = user, context.task
                if (previous is not None and key <= previous) or context.task != (
                    "new" if seen % 2 == 0 else "repeat"
                ):
                    raise ValueError("Context ordering, task pairs or uniqueness differs")
                if seen % 2 and user != previous[0]:
                    raise ValueError("Missing paired task for context user")
                previous = key
                seen += 1
                yield user, context
        if seen != context_result["requests"] or seen != 2 * original["population"]["users"]:
            raise ValueError("Incomplete full-population contexts")

    schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("condition", pa.string()),
            ("target_id", pa.string()),
            ("status", pa.string()),
            ("selection_seconds", pa.float64()),
        ]
    )
    writer = DetailWriter(partial / "selections.parquet", schema=schema)
    try:
        for row in select_all(requests(), policies):
            counts[row["condition"], row["task"], row["target_id"]] += 1
            totals[row["condition"]] += row["selection_seconds"]
            writer.add(row)
    finally:
        writer.close()
    frame = pd.read_csv(root / "metrics.csv")
    common = pd.read_csv(root / "ild.csv")
    rows, ild = replay_rows(frame, common, policies)
    pairs = replay_pairs(frame, pd.read_csv(root / "paired.csv"), policies)
    for policy in policies:
        for metric, value in {
            "selection_seconds": totals[policy.name],
            "selection_latency_mean": totals[policy.name] / context_result["requests"],
            "latency_mean": None,
            "input_tokens": None,
            "output_tokens": None,
            "api_cost": None,
            "llm_calls": None,
            "tool_calls": None,
        }.items():
            rows.append(
                dict(
                    condition=policy.name,
                    aspect="efficiency",
                    task="all",
                    metric=metric,
                    mean=value,
                )
            )
    write_csv(
        partial / "metrics.csv",
        rows,
        first=("condition", "aspect", "task", "window_days", "day_weight", "metric", "k", "mean"),
    )
    write_csv(partial / "ild.csv", ild, first=("condition", "task", "k", "mean"))
    write_csv(
        partial / "paired.csv",
        pairs,
        first=("model_left", "model_right", "task", "window_days", "metric", "k", "mean"),
    )
    write_csv(
        partial / "choices.csv",
        [dict(condition=c, task=t, target_id=s, requests=n) for (c, t, s), n in counts.items()],
    )
    source_costs = frame[(frame.aspect == "efficiency") & frame.condition.isin(versions)].to_dict(
        orient="records"
    )
    write_csv(partial / "source_efficiency.csv", source_costs)
    names = render(partial, rows, ild, original["observations"])
    verify_references([evidence])
    if source_hash() != processing:
        raise ValueError("Selector source changed during execution")
    elapsed = time.perf_counter() - started
    (partial / "run.log").write_text(
        f"SUCCEEDED local selection: {sum(counts.values())} rows; {elapsed:.6f} seconds; external API not run\n"
    )
    atomic_json(
        partial / "result.json",
        dict(
            status="succeeded",
            phase="validation",
            format="music-recsys-local-selector-v1",
            population=original["population"],
            requests_per_condition=context_result["requests"],
            conditions=[p.name for p in policies],
            selection_rows=sum(counts.values()),
            failures=0,
            selection="not_selected",
            llm_evaluation="not_run",
            source_sha256=processing,
            elapsed_seconds=elapsed,
            context_read_seconds=input_seconds,
            figures=names,
            observations=original["observations"],
            definitions=dict(
                quality="Exact task-constant replay; each task uses every user's frozen source rankings and score denominators",
                uncertainty="Inherited paired-user intervals and per-user seed averaging; exact zero differences for identical rankings; exploratory, no multiple comparison correction",
                paired_difference="model_right minus model_left; task-rule is not an independent new source model",
                ild="Inherited common-user intersection across all original EASE/BPR conditions and seeds",
                efficiency="Measured Python selection only; source_efficiency.csv is original all-task GPU batch cost, not rule inference latency; context IO and report duration are separate",
                limitation="Only fixed and task-only policies; no user-dependent rules or LLM evaluation",
            ),
            artifacts={p.name: dict(path=p.name, sha256=file_sha256(p)) for p in partial.iterdir()},
        ),
    )
    os.rename(partial, output)
    return output
