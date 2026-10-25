"""Read-only ALS inventory, comparable cohorts and publication evidence."""

import csv
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.store import atomic_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect(method):
    records = []
    for path in sorted(Path(method).rglob("result.json")):
        result = read(path)
        if result.get("format") != "music-recsys-full-result-v1":
            continue
        if result.get("status") != "succeeded" or result.get("model") != "als":
            raise ValueError(f"Incomplete ALS source: {path}")
        config_path = path.with_name("config.json")
        config = read(config_path)
        verify_artifacts(path.parent, result["artifacts"])
        execution = config["execution"]
        parameters = execution.get("training_parameters", config["settings"]["parameters"])
        if result["primary"] != config["settings"]["evaluation"]:
            raise ValueError("Saved evaluation mismatch")
        records.append(
            dict(
                condition=path.parent.relative_to(method).as_posix(),
                phase=result["phase"],
                factors=parameters["factors"],
                alpha=parameters["alpha"],
                regularization=parameters["regularization"],
                seed=config["settings"]["seeds"][0],
                backend=execution.get("backend"),
                use_gpu=parameters.get("use_gpu", False),
                solver=execution["solver"],
                parameters=parameters,
                config=str(config_path),
                config_sha256=file_sha256(config_path),
                result_path=str(path),
                result_sha256=file_sha256(path),
                source_sha256=config["source_sha256"],
                dependency_lock_sha256=config["dependency_lock_sha256"],
                saved=config,
                result=result,
            )
        )
    return records


def select_groups(records):
    lookup = {r["condition"]: r for r in records}
    groups = {
        "validation": [
            lookup[p]
            for p in (
                "32/validation",
                "64/validation",
                "128/validation",
                "bridge/f256-s0",
                "bridge/f512-s0",
                "1024",
            )
        ],
        "test": [lookup[f"{f}/test"] for f in (32, 64, 128, 256, 512)],
    }
    for phase, group in groups.items():
        common = None
        for record in group:
            saved, result = record["saved"], record["result"]
            params = record["parameters"]
            execution = saved["execution"]
            if result["phase"] != phase or not params.get("use_gpu"):
                raise ValueError("A factor comparison requires the stated phase and GPU solver")
            if execution["evaluation_device"] != "cuda" or params.get("cg_steps") != 20:
                raise ValueError("GPU metrics and CG20 are required")
            identity = dict(
                manifest=saved["manifest"],
                features=saved["features_sha256"],
                dependency_lock=saved.get("dependency_lock_sha256"),
                population=result["population"],
                evaluation=result["primary"],
                parameters={k: v for k, v in params.items() if k != "factors"},
                seed=record["seed"],
                execution={
                    k: execution.get(k)
                    for k in (
                        "solver",
                        "metric_dtype",
                        "implicit",
                        "cupy",
                        "numpy",
                        "gpu_inventory",
                        "evaluation_batch_size",
                        "threads",
                    )
                },
            )
            if common is not None and identity != common:
                raise ValueError(f"Incomparable {phase} factor conditions")
            common = identity
    return groups


def score_audit(record, xp=np):
    """Recompute every saved mean/count and reject malformed individual score arrays."""
    import pyarrow.parquet as pq

    result = record["result"]
    path = Path(record["result_path"]).parent / result["artifacts"]["scores"]["path"]
    ks = result["primary"]["ks"]
    expected = {}
    for row in result["accuracy"]:
        key = row["task"], row["window_days"], row["day_weight"], row["metric"]
        expected.setdefault(key, {})[row["k"]] = row
    verified = 0
    for (task, window, weight, metric), rows in expected.items():
        table = pq.read_table(
            path,
            filters=[
                ("task", "=", task),
                ("window_days", "=", window),
                ("day_weight", "=", -1.0 if weight is None else weight),
                ("metric", "=", metric),
            ],
            columns=["user_id", "values"],
        )
        users = table["user_id"].to_pylist()
        if len(set(users)) != len(users):
            raise ValueError("Duplicate score users")
        raw = table["values"].to_pylist()
        if any(len(v) != len(ks) for v in raw):
            raise ValueError("Score array does not match saved K")
        values = xp.asarray(np.asarray(raw, dtype=float).reshape(-1, len(ks)))
        if not bool(xp.isfinite(values).all()):
            raise ValueError("Non-finite individual score")
        means = values.mean(axis=0) if len(users) else None
        for i, k in enumerate(ks):
            row = rows[k]
            if row["n_evaluated"] != len(users) or row["n_excluded"] != result["population"][
                "users"
            ] - len(users):
                raise ValueError("Score population mismatch")
            actual = float(means[i]) if means is not None else None
            if (actual is None) != (row["mean"] is None) or (
                actual is not None and not np.isclose(actual, row["mean"], atol=1e-12, rtol=1e-10)
            ):
                raise ValueError("Saved score mean mismatch")
            verified += 1
    return verified


def source_map(group):
    return {
        str(r["factors"]): dict(
            path=Path(r["result_path"]).parent
            / r["result"]["artifacts"]["recommendations"]["path"],
            scores=Path(r["result_path"]).parent / r["result"]["artifacts"]["scores"]["path"],
            population=r["result"]["population"],
        )
        for r in group
    }


def common_ild(group, xp=np):
    """Use the intersection of users with valid features at each task and cutoff."""
    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.research.complementarity import FeaturePrefixes, recommendation_batches

    saved = group[0]["saved"]
    manifest = saved["manifest"]
    feature_path = Path(saved["settings"]["features"])
    if file_sha256(feature_path) != saved["features_sha256"]:
        raise ValueError("Feature source changed")
    features = load_features(
        feature_path,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    # Include every observed recommendation ID; feature validity is independent of catalog order
    sources = source_map(group)
    import pyarrow.parquet as pq

    items = sorted(
        {
            item
            for source in sources.values()
            for row in pq.read_table(source["path"], columns=["item_ids"])["item_ids"].to_pylist()
            for item in row
        }
    )
    index = {item: i for i, item in enumerate(items)}
    prefixes = FeaturePrefixes(features, items, xp)
    ks = group[0]["result"]["primary"]["ks"]
    positions = xp.asarray(ks) - 1
    total = xp.zeros((2, len(group), len(ks)), dtype=xp.float64)
    counts = xp.zeros((2, len(ks)), dtype=xp.int64)
    seen = 0
    for batch in recommendation_batches(sources, 128):
        arrays = np.full((len(batch), len(group), 20), -1, dtype=np.int32)
        for i, request in enumerate(batch):
            for j, row in enumerate(request):
                arrays[i, j, : len(row["item_ids"])] = [index[item] for item in row["item_ids"]]
        values, valid = prefixes.calculate(xp.asarray(arrays))
        values, valid = values[:, :, positions], valid[:, :, positions]
        common = valid.all(axis=1)
        for task in range(2):
            mask = common[task::2]
            total[task] += (values[task::2] * mask[:, None, :]).sum(axis=0)
            counts[task] += mask.sum(axis=0)
        seen += len(batch) // 2
    if seen != group[0]["result"]["population"]["users"]:
        raise ValueError("Recommendation population mismatch")
    return [
        dict(
            phase=group[0]["phase"],
            factors=r["factors"],
            task=task,
            k=k,
            mean=float(total[t, m, p] / counts[t, p]) if int(counts[t, p]) else None,
            n_evaluated=int(counts[t, p]),
            n_excluded=seen - int(counts[t, p]),
        )
        for t, task in enumerate(("new", "repeat"))
        for m, r in enumerate(group)
        for p, k in enumerate(ks)
    ]


def write_csv(path, rows, first=()):
    columns = list(first) + sorted({k for r in rows for k in r} - set(first))
    with Path(path).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def publish(method, *, metric_rows, save_figures):
    import cupy as cp

    from src.infrastructure.provenance import source_hash
    from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu
    from src.infrastructure.research.complementarity import paired_score_differences

    method = Path(method).resolve()
    output = method / "paper"
    if output.exists():
        raise FileExistsError("ALS paper output exists")
    print("Analysis GPU preflight: " + require_idle_gpu(), flush=True)
    records = collect(method)
    groups = select_groups(records)
    print(f"Verified {len(records)} saved ALS conditions", flush=True)
    rows, inventory = [], []
    for r in records:
        meta = {
            k: r[k]
            for k in (
                "condition",
                "phase",
                "factors",
                "alpha",
                "regularization",
                "seed",
                "backend",
                "use_gpu",
                "solver",
            )
        }
        inventory.append(
            dict(
                meta,
                config=r["config"],
                config_sha256=r["config_sha256"],
                result=r["result_path"],
                result_sha256=r["result_sha256"],
                source_sha256=r["source_sha256"],
                dependency_lock_sha256=r["dependency_lock_sha256"],
            )
        )
        rows.extend(
            dict(meta, **row, source_result=r["result_path"], source_sha256=r["result_sha256"])
            for row in metric_rows(r["result"])
        )
    verified, paired, ild = 0, [], []
    for phase, group in groups.items():
        print(f"Auditing {phase}: scores, paired differences and common ILD", flush=True)
        verified += sum(score_audit(r, cp) for r in group)
        ks = group[0]["result"]["primary"]["ks"]
        paired.extend(
            dict(phase=phase, **r)
            for r in paired_score_differences(
                source_map(group), ks, windows=(30, 90, 180), xp=cp, ci_ks=(20,)
            )
        )
        ild.extend(common_ild(group, cp))
        cp.get_default_memory_pool().free_all_blocks()
    with tempfile.TemporaryDirectory(prefix="als-paper-", dir=method) as temporary:
        stage = Path(temporary)
        write_csv(
            stage / "conditions.csv",
            inventory,
            ("phase", "condition", "factors", "alpha", "regularization", "seed"),
        )
        write_csv(
            stage / "metrics.csv",
            rows,
            (
                "phase",
                "condition",
                "factors",
                "alpha",
                "regularization",
                "seed",
                "aspect",
                "task",
                "metric",
                "k",
                "mean",
            ),
        )
        write_csv(
            stage / "paired.csv",
            paired,
            (
                "phase",
                "model_left",
                "model_right",
                "task",
                "metric",
                "k",
                "mean",
                "ci_low",
                "ci_high",
            ),
        )
        write_csv(
            stage / "ild_common.csv",
            ild,
            ("phase", "factors", "task", "k", "mean", "n_evaluated", "n_excluded"),
        )
        atomic_json(
            stage / "config.json",
            dict(
                format="music-recsys-als-paper-config-v1",
                source_sha256=source_hash(),
                display=dict(window_days=90, day_weight=1, k=20),
                groups={phase: [r["condition"] for r in group] for phase, group in groups.items()},
                policy="Validation exploration after Test; original Test retained separately; no model selected",
                ci="2000 user bootstrap resamples, seed42, unadjusted 95%; single training seed0; not training-seed uncertainty",
                ild="Common valid-user intersection within each phase/task/K; conditional on feature completeness",
            ),
        )
        figures = save_figures(stage, groups, records, ild)
        for r in records:
            if (
                file_sha256(r["config"]) != r["config_sha256"]
                or file_sha256(r["result_path"]) != r["result_sha256"]
            ):
                raise ValueError("Source changed during publication")
        (stage / "run.log").write_text(
            f"SUCCEEDED {len(records)} source conditions; {verified} accuracy means verified; full recommendation populations checked; GPU paired differences and common ILD; original evidence preserved\n",
            encoding="utf-8",
        )
        artifacts = {
            p.name: dict(path=p.name, sha256=file_sha256(p)) for p in stage.iterdir() if p.is_file()
        }
        atomic_json(
            stage / "result.json",
            dict(
                format="music-recsys-als-paper-v1",
                status="succeeded",
                selection="not_selected",
                source_conditions=len(records),
                verified_accuracy_rows=verified,
                sources=inventory,
                groups={phase: [r["condition"] for r in group] for phase, group in groups.items()},
                figures=figures,
                artifacts=artifacts,
            ),
        )
        os.replace(stage, output)
    return output
