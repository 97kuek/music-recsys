"""Compare explicit EASE input variants using aligned saved scores and rankings."""

import os
import time

import numpy as np

from backend.infrastructure.datasets.features import load_features
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.complementarity import paired_score_differences
from backend.infrastructure.research.hybrid_config import read, reference, verify_references
from backend.infrastructure.research.metrics import metric_rows
from backend.infrastructure.research.personal_diversity import diversity
from backend.infrastructure.research.results import verify_artifacts
from backend.infrastructure.research.store import atomic_json


def run(config, references, *, xp, execution, render, name="comparison", phase="validation"):

    if phase not in {"validation", "test"}:
        raise ValueError("Unknown comparison phase")
    if name not in {"comparison", "history-control"}:
        raise ValueError("Unknown comparison output")
    output, partial = config.output / name, config.output / (name + ".partial")
    if output.exists() or partial.exists():
        raise FileExistsError("Existing comparison must be preserved")
    target = (
        "history-" + config.parameters["history_basis"].replace("_", "-")
        if config.model == "history_frequency"
        else config.name
        if config.model in {"transition_rules", "esasrec"}
        else "log-count-l500"
    )
    if not references or target in references:
        raise ValueError("Explicit distinct reference names required")
    directories = {**references, target: config.output}
    started, processing = time.perf_counter(), source_hash()
    evidence, candidates, sources, rows = {}, {}, {}, []
    identity = None
    for name, directory in directories.items():
        saved, result = read(directory / "config.json"), read(directory / "result.json")
        if (
            result["status"] != "succeeded"
            or result["phase"] != phase
            or result["model"] not in {"ease", "history_frequency", "transition_rules", "esasrec"}
        ):
            raise ValueError("Completed supported model with matching phase required")
        current = (
            saved["manifest"],
            saved["features_sha256"],
            result["population"],
            saved["settings"]["evaluation"],
        )
        if identity is not None and current != identity:
            raise ValueError("Input population, features or evaluation differs")
        identity = current
        if name == target and (
            result["model"] != config.model or result["model_parameters"] != config.parameters
        ):
            raise ValueError("Target parameters differ from explicit experiment config")
        verify_artifacts(directory, result["artifacts"])
        evidence.update({f"{name}/{key}": value for key, value in reference(directory).items()})
        for key, artifact in result["artifacts"].items():
            evidence[f"{name}/{key}"] = dict(
                path=str(directory / artifact["path"]), sha256=artifact["sha256"]
            )
        sources[name] = dict(
            scores=str(directory / "scores.parquet"), population=result["population"]
        )
        candidates[name] = dict(
            runs=[
                dict(
                    condition=dict(name=name),
                    artifacts=dict(
                        recommendations=dict(path=str(directory / "recommendations.parquet"))
                    ),
                )
            ]
        )
        rows.extend(dict(condition=name, **row) for row in metric_rows(result))
    manifest, _, population, _ = identity
    # All methods share this immutable training catalog, independent of weights.
    import duckdb

    dataset = config.validation_dataset if phase == "validation" else config.test_dataset
    learning = dataset / manifest["files"]["learning"]["path"]
    if file_sha256(learning) != manifest["files"]["learning"]["sha256"]:
        raise ValueError("Learning file changed")
    with duckdb.connect(config={"threads": 2, "memory_limit": "8GB"}) as connection:
        catalog = connection.execute(
            "SELECT item_id,count(*) AS users FROM read_parquet(?) GROUP BY item_id ORDER BY item_id",
            [str(learning)],
        ).fetch_arrow_table()
        users = (
            connection.execute(
                "SELECT DISTINCT user_id FROM read_parquet(?) ORDER BY user_id", [str(learning)]
            )
            .fetchnumpy()["user_id"]
            .tolist()
        )
    if len(users) != population["users"] or catalog.num_rows != population["items"]:
        raise ValueError("Catalog population differs")
    features = load_features(
        config.features,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    evidence["learning"] = dict(path=str(learning), sha256=manifest["files"]["learning"]["sha256"])
    evidence["features"] = dict(path=str(config.features), sha256=file_sha256(config.features))
    partial.mkdir()
    atomic_json(
        partial / "config.json",
        dict(
            phase=phase,
            source_sha256=processing,
            evidence=evidence,
            execution=execution,
            references={name: str(path) for name, path in directories.items()},
            selection="not_selected",
            hypothesis=(
                f"personal {config.parameters['history_basis']} versus saved controls; identical catalog and raw-count popularity ties"
                if config.model == "history_frequency"
                else "time-weighted transition rules versus saved controls"
                if config.model == "transition_rules"
                else "causal sequence model versus saved controls"
                if config.model == "esasrec"
                else "log2 learning counts versus explicit saved controls"
            ),
        ),
    )
    ks = list(config.evaluation.ks)
    with (partial / "run.log").open("x") as log:

        def progress(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        pairs = paired_score_differences(
            sources, ks, windows=tuple(config.evaluation.window_days), xp=xp, ci_ks=(20,)
        )
        choices = np.broadcast_to(
            np.arange(len(candidates))[None, :, None], (2, len(candidates), len(users))
        ).copy()
        recomputed, common = diversity(
            candidates,
            users,
            choices,
            catalog,
            features,
            ks,
            xp=xp,
            progress=progress,
            policies=tuple(candidates),
        )
        checks, maximum = 0, 0.0
        for row in recomputed:
            matches = [
                r
                for r in rows
                if r["aspect"] == "diversity"
                and all(r.get(k) == row[k] for k in ("condition", "task", "metric", "k"))
            ]
            if len(matches) != 1 or (matches[0]["mean"] is None) != (row["mean"] is None):
                raise ValueError("Diversity reference mismatch")
            if row["mean"] is not None:
                error = abs(row["mean"] - matches[0]["mean"])
                maximum = max(maximum, error)
                if error > 1e-10:
                    raise ValueError("Diversity replay differs")
            checks += 1
        for name, values in (("metrics", rows), ("paired", pairs), ("ild", common)):
            write_csv(partial / f"{name}.csv", values)
        figures = render(partial, rows, common, result["observations"])
        verify_references([evidence])
        if source_hash() != processing:
            raise ValueError("Comparison source changed")
        progress("SUCCEEDED: source alignment, paired accuracy and common ILD verified")
    atomic_json(
        partial / "result.json",
        dict(
            status="succeeded",
            phase=phase,
            population=population,
            source_sha256=processing,
            elapsed_seconds=time.perf_counter() - started,
            figures=figures,
            definitions=dict(
                intervals="2000 paired-user bootstrap; K20/90d; exploratory, no multiple comparison correction",
                ild="all explicitly compared methods, common feature-valid users",
                timing="original measurements, separate execution dates and input RAM budgets; no speed superiority claim",
            ),
            diversity_checks=checks,
            max_diversity_error=maximum,
            selection="not_selected",
            external_api="not_run",
            artifacts={
                p.name: dict(path=p.name, sha256=file_sha256(p))
                for p in partial.iterdir()
                if p.is_file()
            },
        ),
    )
    os.rename(partial, output)
    return output
