"""All-user exploratory selection experiment; no API or Test access."""

import os
import shutil
import time

import numpy as np

from backend.domain.recommendation.personal_selector import FEATURES, PersonalPolicy
from backend.infrastructure.datasets.features import load_features
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import verify_references
from backend.infrastructure.research.personal_diversity import diversity
from backend.infrastructure.research.personal_inputs import inputs, score_blocks
from backend.infrastructure.research.personal_metrics import accuracy
from backend.infrastructure.research.personal_rules import POLICIES, crossfit, predict
from backend.infrastructure.research.store import atomic_json


def check_fixed(rows, root):
    """Reconstructed fixed controls must reproduce every original mean and denominator."""
    import pandas as pd

    frame = pd.read_csv(root / "metrics.csv").fillna({"day_weight": -1, "window_days": -1})
    checks, maximum = 0, 0.0
    for row in rows:
        if row["condition"] not in POLICIES[:4] or row["aspect"] == "efficiency":
            continue
        selected = frame[
            (frame.condition == row["condition"])
            & (frame.task == row["task"])
            & (frame.metric == row["metric"])
            & (frame.k == row["k"])
        ]
        if row["aspect"] == "accuracy":
            selected = selected[
                (selected.window_days == row["window_days"])
                & (
                    selected.day_weight
                    == (row["day_weight"] if row["day_weight"] is not None else -1)
                )
            ]
        if len(selected) != 1:
            raise ValueError("Original fixed-control metric missing or ambiguous")
        original = selected.iloc[0]
        if row["mean"] is None:
            if not pd.isna(original["mean"]):
                raise ValueError("Fixed-control missingness differs")
        else:
            error = abs(row["mean"] - original["mean"])
            maximum = max(maximum, error)
            if error > 1e-10:
                raise ValueError(
                    f"Fixed-control value differs: {row['condition']} {row['metric']} {error}"
                )
        if row["aspect"] == "accuracy" and row["n_evaluated"] != int(original.n_evaluated):
            raise ValueError("Fixed-control accuracy denominator differs")
        checks += 1
    return dict(checks=checks, max_absolute_error=maximum)


def run(config, *, xp, execution, render, include_tree=False):
    import pyarrow as pa
    import pyarrow.parquet as pq

    policies = (*POLICIES, "tree-ndcg", "tree-recall") if include_tree else POLICIES
    root = config.output
    name = "selector-tree" if include_tree else "selector-personal"
    output, partial = root / name, root / (name + ".partial")
    if output.exists() or partial.exists():
        raise FileExistsError("Preserve existing personal-selection results")
    if shutil.disk_usage(root).free < 100 * 1024**2:
        raise OSError("At least 100 MiB free required for compact reports")
    print("Verifying candidate hashes and full-population contexts", flush=True)
    started, processing = time.perf_counter(), source_hash()
    saved, original, candidates, evidence, users, contexts = inputs(root)
    if tuple(candidates) != POLICIES[:4]:
        raise ValueError("Candidate tie order differs")
    for name in ("catalog.parquet", "metrics.csv", "result.json", "config.json"):
        evidence[f"comparison/{name}"] = dict(
            path=str(root / name), sha256=file_sha256(root / name)
        )
    evidence["input_config"] = dict(path=str(config.path), sha256=file_sha256(config.path))
    ks = list(config.evaluation.ks)
    if ks != list(range(2, 21)):
        raise ValueError("Full K2-20 sensitivity required")
    n = len(users)
    folds = np.empty(n, dtype=np.int8)
    folds[np.random.default_rng(42).permutation(n)] = np.arange(n) % 2
    partial.mkdir()
    design = dict(
        phase="validation",
        source_sha256=processing,
        evidence=evidence,
        candidates=candidates,
        execution=dict(
            execution,
            dtype="float64",
            seed=42,
            solver="two-fold policy selection; GPU metric replay",
            training=False,
        ),
        selection="not_selected",
        external_transmission=False,
        design=dict(
            folds=2,
            split="balanced shuffled users; seed42; paired tasks share fold",
            features=list(FEATURES),
            objectives={
                "new": ["binary_ndcg", "recall"],
                "repeat": ["graded_ndcg", "repeat_recall"],
            },
            fitting_window_days=90,
            fitting_k=20,
            fitting_day_weight=1,
            fitting_condition="exploratory display-anchor objectives, not a selected primary metric; fixed across evaluation K/windows",
            thresholds="training-user inverse-CDF quartiles",
            minimum_eligible_leaf=200,
            complexity="depth two added to one-split controls"
            if include_tree
            else "constant or one split",
            depth_search=False,
            tie_order=list(candidates),
            uncertainty="2000 paired-user bootstrap; fitted cross-fold rules held fixed; no refitting or multiplicity correction",
            oracle="per-user per-metric per-K seed-average maximum using future truth; unattainable diagnostic, no deployment claim",
            limitations="candidate design already informed by this Validation; user cross-fitting is exploratory, not independent temporal Test",
        ),
    )
    atomic_json(partial / "config.json", design)
    with (partial / "run.log").open("x") as log:

        def progress(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        blocks = score_blocks(candidates, users, ks, progress)
        choices = np.zeros((2, len(policies), n), dtype=np.int8)
        choices[:, :4] = np.arange(4)[None, :, None]
        choices[1, 4] = 1
        rules, timings, trees = [], [], []
        for t, task in enumerate(("new", "repeat")):
            x = contexts[t][list(FEATURES)].to_numpy(dtype=float)
            for objective, metric in enumerate(
                ("binary_ndcg", "recall") if t == 0 else ("graded_ndcg", "repeat_recall")
            ):
                key = (
                    "discovery" if t == 0 else "repeat",
                    90,
                    1.0 if metric == "graded_ndcg" else -1.0,
                    metric,
                )
                indices, values = blocks[key]
                outcome = xp.full((n, 4), xp.nan, dtype=xp.float64)
                outcome[xp.asarray(indices)] = xp.asarray(values[:, :, ks.index(20)])
                tick = time.perf_counter()
                selected, constant, fitted = crossfit(x, outcome, folds, xp=xp)
                xp.cuda.Stream.null.synchronize()
                fit_seconds = time.perf_counter() - tick
                choices[t, 5 + objective], choices[t, 7 + objective] = selected, constant
                tick = time.perf_counter()
                check = np.empty(n, dtype=np.int8)
                for record in fitted:
                    test = folds == record["held_fold"]
                    check[test] = predict(PersonalPolicy(**record["rule"]), x[test])
                    rules.append(dict(task=task, objective=metric, **record))
                inference_seconds = time.perf_counter() - tick
                if not np.array_equal(check, selected):
                    raise ValueError("Saved rules fail to reproduce held-user selections")
                timings.append(
                    dict(
                        task=task,
                        condition=POLICIES[5 + objective],
                        fitting_seconds=fit_seconds,
                        selection_seconds=inference_seconds,
                        selection_latency_mean=inference_seconds / n,
                    )
                )
                if include_tree:
                    from backend.infrastructure.research.personal_tree import (
                        crossfit_tree,
                        predict_tree,
                    )

                    tick = time.perf_counter()
                    tree_choices, fitted_trees = crossfit_tree(x, outcome, folds, xp=xp)
                    xp.cuda.Stream.null.synchronize()
                    tree_fit_seconds = time.perf_counter() - tick
                    choices[t, 9 + objective] = tree_choices
                    tick = time.perf_counter()
                    for record in fitted_trees:
                        test = folds == record["held_fold"]
                        check[test] = predict_tree(record["tree"], x[test])
                        trees.append(dict(task=task, objective=metric, **record))
                    tree_seconds = time.perf_counter() - tick
                    if not np.array_equal(check, tree_choices):
                        raise ValueError("Tree replay differs")
                    timings.append(
                        dict(
                            task=task,
                            condition=policies[9 + objective],
                            fitting_seconds=tree_fit_seconds,
                            selection_seconds=tree_seconds,
                            selection_latency_mean=tree_seconds / n,
                        )
                    )
                progress(f"Fitted held-user rule: {task}/{metric}")
        atomic_json(partial / "rules.json", rules)
        if include_tree:
            atomic_json(partial / "trees.json", trees)
        write_csv(partial / "timing.csv", timings)
        # Compact auditable assignments; source rankings/scores remain referenced rather than copied.
        records = []
        for t, task in enumerate(("new", "repeat")):
            table = pa.table(
                dict(
                    user_id=users,
                    task=[task] * n,
                    held_fold=folds,
                    **{
                        policies[p]: [POLICIES[i] for i in choices[t, p]]
                        for p in range(5, len(policies))
                    },
                )
            )
            records.append(table)
        pq.write_table(
            pa.concat_tables(records), partial / "selections.parquet", compression="zstd"
        )
        progress("Evaluating all windows, weights, K and metric-specific oracle bounds")
        rows, pairs, oracle = accuracy(blocks, choices, ks, xp=xp, population=n, policies=policies)
        del blocks
        catalog = pq.read_table(root / "catalog.parquet")
        manifest = saved["manifest"]
        features = load_features(
            config.features,
            dataset_id=manifest["dataset_id"],
            snapshot_id=manifest["source"]["snapshot_id"],
        )
        diverse, common = diversity(
            candidates,
            users,
            choices,
            catalog,
            features,
            ks,
            xp=xp,
            progress=progress,
            policies=policies,
        )
        rows.extend(diverse)
        verification = check_fixed(rows, root)
        for policy in policies:
            for metric in (
                "latency_mean",
                "api_cost",
                "input_tokens",
                "output_tokens",
                "llm_calls",
                "tool_calls",
            ):
                rows.append(
                    dict(
                        condition=policy, task="all", aspect="efficiency", metric=metric, mean=None
                    )
                )
        for timing in timings:
            rows.append(
                dict(
                    condition=timing["condition"],
                    task=timing["task"],
                    aspect="efficiency",
                    metric="selection_latency_mean",
                    mean=timing["selection_latency_mean"],
                )
            )
        counts = [
            dict(condition=p, task=task, target_id=target, requests=int((choices[t, j] == i).sum()))
            for t, task in enumerate(("new", "repeat"))
            for j, p in enumerate(policies)
            for i, target in enumerate(POLICIES[:4])
        ]
        for name, data in (
            ("metrics", rows),
            ("paired", pairs),
            ("oracle", oracle),
            ("ild", common),
            ("choices", counts),
            ("observations", original["observations"]),
        ):
            write_csv(partial / f"{name}.csv", data)
        figures = render(partial, rows, common, original["observations"])
        progress("Rechecking source integrity before publishing")
        verify_references([evidence])
        if source_hash() != processing:
            raise ValueError("Source changed while experiment was running")
        elapsed = time.perf_counter() - started
        progress(f"SUCCEEDED: {n} users; {elapsed:.3f} seconds; no API calls")
    atomic_json(
        partial / "result.json",
        dict(
            status="succeeded",
            phase="validation",
            format="music-recsys-personal-selector-v1",
            population=original["population"],
            requests_per_condition=2 * n,
            failures=0,
            source_sha256=processing,
            selection="not_selected",
            llm_evaluation="not_run",
            elapsed_seconds=elapsed,
            verification=verification,
            figures=figures,
            observations=original["observations"],
            definitions=dict(
                ild="common intersection across four candidates and all seed versions; differs from original six-group intersection",
                coverage="recomputed catalog exposure separately for each seed, then averaged",
                efficiency="vectorized CPU rule application including fold dispatch only; IO/recommendation/API latency not measured",
                scores="three fixed BPR seeds averaged per user before fitting/evaluation; EASE has one seed",
                interpretation="exploratory cross-fitted Validation; no Test or external API used; oracle uses future truth",
            ),
            artifacts={
                p.name: dict(path=p.name, sha256=file_sha256(p))
                for p in partial.iterdir()
                if p.is_file()
            },
        ),
    )
    os.rename(partial, output)
    return output
