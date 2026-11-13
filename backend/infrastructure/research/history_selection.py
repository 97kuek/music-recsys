"""Cross-fitted two-method history selection with full saved-score and ranking replay."""

import os
import time

import numpy as np

from backend.infrastructure.datasets.features import load_features
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.history_policy import FEATURES, POLICIES, crossfit, predict
from backend.infrastructure.research.hybrid_config import read, reference, verify_references
from backend.infrastructure.research.personal_diversity import diversity
from backend.infrastructure.research.personal_metrics import interval
from backend.infrastructure.research.results import verify_artifacts
from backend.infrastructure.research.store import atomic_json


def run(config, *, xp, execution, render):
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = config.output.parent
    output = config.output / "selector"
    partial = output.with_name("selector.partial")
    if output.exists() or partial.exists():
        raise FileExistsError("Preserve existing selector output")
    started, processing = time.perf_counter(), source_hash()
    paths = [root / name for name in POLICIES[:2]]
    saved, results = (
        [read(p / "config.json") for p in paths],
        [read(p / "result.json") for p in paths],
    )
    evidence, candidates = {}, {}
    for i, (p, c, r) in enumerate(zip(paths, saved, results, strict=True)):
        if r["phase"] != "validation" or r["status"] != "succeeded":
            raise ValueError("Validation only")
        if (
            any(c[k] != saved[0][k] for k in ["manifest", "features_sha256"])
            or r["primary"] != results[0]["primary"]
            or r["population"] != results[0]["population"]
        ):
            raise ValueError("Aligned sources required")
        verify_artifacts(p, r["artifacts"])
        evidence.update({f"{i}/{k}": v for k, v in reference(p).items()})
        for name, artifact in r["artifacts"].items():
            evidence[f"{i}/{name}"] = dict(
                path=str(p / artifact["path"]), sha256=artifact["sha256"]
            )
        candidates[POLICIES[i]] = dict(
            runs=[
                dict(
                    condition=dict(name=POLICIES[i]),
                    artifacts=dict(recommendations=dict(path=str(p / "recommendations.parquet"))),
                )
            ]
        )
    if results[1]["model_parameters"] != config.parameters:
        raise ValueError("Target config differs")
    learning = config.validation_dataset / "learning.parquet"
    digest = file_sha256(learning)
    if digest != saved[0]["manifest"]["files"]["learning"]["sha256"]:
        raise ValueError("Learning hash differs")
    evidence["learning"] = dict(path=str(learning), sha256=digest)
    evidence["features"] = dict(path=str(config.features), sha256=file_sha256(config.features))
    with duckdb.connect(config={"threads": 2, "memory_limit": "8GB"}) as c:
        history = c.execute(
            "SELECT user_id,count(*) tracks,sum(play_count) plays,sum(active_days) item_days,max(play_count)::DOUBLE/sum(play_count) top_share FROM read_parquet(?) GROUP BY user_id ORDER BY user_id",
            [str(learning)],
        ).fetchdf()
        catalog = c.execute(
            "SELECT item_id,count(*) users FROM read_parquet(?) GROUP BY item_id ORDER BY item_id",
            [str(learning)],
        ).fetch_arrow_table()
    users = history.user_id.tolist()
    n = len(users)
    x = history[list(FEATURES)].to_numpy(dtype=float)
    if n != results[0]["population"]["users"] or not np.isfinite(x).all():
        raise ValueError("Full valid training population required")
    lookup = {u: i for i, u in enumerate(users)}
    folds = np.empty(n, np.int8)
    folds[np.random.default_rng(42).permutation(n)] = np.arange(n) % 2
    tables = [pq.read_table(p / "scores.parquet") for p in paths]
    keys = ["user_id", "task", "window_days", "day_weight", "metric"]
    if not tables[0].select(keys).equals(tables[1].select(keys)):
        raise ValueError("Score identities differ")
    ks = list(config.evaluation.ks)
    if ks != list(range(2, 21)):
        raise ValueError("Full K sensitivity required")
    meta = tables[0].select(keys).to_pandas()
    values = []
    for t in tables:
        flat = t["values"].combine_chunks()
        if not np.all(np.diff(flat.offsets.to_numpy()) == len(ks)):
            raise ValueError("K shape differs")
        v = flat.values.to_numpy().reshape(-1, len(ks))
        if not np.isfinite(v).all():
            raise ValueError("Finite scores required")
        values.append(v)
    blocks = {}
    for key, ix in meta.groupby(keys[1:]).indices.items():
        ids = np.array([lookup[u] for u in meta.iloc[ix].user_id])
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate users")
        blocks[key] = (ids, np.stack([v[ix] for v in values], axis=1))
    partial.mkdir()
    atomic_json(
        partial / "config.json",
        dict(
            source_sha256=processing,
            evidence=evidence,
            execution=execution,
            features=list(FEATURES),
            policies=list(POLICIES),
            folds=2,
            seed=42,
            thresholds="train-user inverse-CDF quartiles",
            minimum_eligible_leaf=200,
            depth=1,
            objectives=["graded_ndcg", "repeat_recall"],
            fitting_window_days=90,
            fitting_k=20,
            fitting_day_weight=1,
            selection="not_selected",
            test_access=False,
            limitations="exploratory cross-fit after candidate development; not independent temporal confirmation; intervals hold fitted rules fixed",
        ),
    )
    choices = np.zeros((2, len(POLICIES), n), np.int8)
    choices[:, 1] = 1
    rules, timings = [], []
    for objective, metric in enumerate(["graded_ndcg", "repeat_recall"]):
        ids, v = blocks[("repeat", 90, 1.0 if objective == 0 else -1.0, metric)]
        y = xp.full((n, 2), xp.nan)
        y[xp.asarray(ids)] = xp.asarray(v[:, :, ks.index(20)])
        tick = time.perf_counter()
        selected, constant, records = crossfit(x, y, folds, xp=xp)
        xp.cuda.Stream.null.synchronize()
        fit_seconds = time.perf_counter() - tick
        choices[1, 2 + objective], choices[1, 4 + objective] = selected, constant
        tick = time.perf_counter()
        replayed = np.empty(n, np.int8)
        for record in records:
            held = folds == record["held_fold"]
            replayed[held] = predict(record["rule"], x[held])
            rules.append(dict(objective=metric, **record))
        latency = (time.perf_counter() - tick) / n
        if not np.array_equal(replayed, selected):
            raise ValueError("Rule replay differs")
        timings.append(
            dict(
                condition=POLICIES[2 + objective],
                fit_seconds=fit_seconds,
                selection_latency_mean=latency,
            )
        )
    pq.write_table(
        pa.Table.from_pydict(
            dict(user_id=users, fold=folds, **{p: choices[1, j] for j, p in enumerate(POLICIES)})
        ),
        partial / "selections.parquet",
        compression="zstd",
    )
    atomic_json(partial / "rules.json", rules)
    rows, pairs = [], []
    with (partial / "run.log").open("x") as log:

        def progress(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        progress("Cross-fitting complete; replaying all scores")
        for key, (ids, v) in blocks.items():
            task, window, weight, metric = key
            ti = int(task == "repeat")
            a = xp.asarray(v)
            selected = xp.asarray(choices[ti][:, ids].T)
            scores = a[xp.arange(len(ids))[:, None], selected]
            means = xp.asnumpy(scores.mean(axis=0))
            for j, p in enumerate(POLICIES):
                for pos, k in enumerate(ks):
                    rows.append(
                        dict(
                            condition=p,
                            aspect="accuracy",
                            task=task,
                            window_days=int(window),
                            day_weight=None if weight < 0 else float(weight),
                            metric=metric,
                            k=k,
                            mean=float(means[j, pos]),
                            n_evaluated=len(ids),
                            n_excluded=n - len(ids),
                        )
                    )
                    if j < 2:
                        original = next(
                            r
                            for r in results[j]["accuracy"]
                            if r["task"] == task
                            and r["window_days"] == window
                            and (r["day_weight"] if r["day_weight"] is not None else -1) == weight
                            and r["metric"] == metric
                            and r["k"] == k
                        )
                        if (
                            abs(means[j, pos] - original["mean"]) > 1e-12
                            or len(ids) != original["n_evaluated"]
                        ):
                            raise ValueError("Fixed score replay differs")
            if task == "repeat" and window == 90 and weight in (-1, 1):
                for policy in [2, 3]:
                    d = scores[:, policy, ks.index(20)] - scores[:, 1, ks.index(20)]
                    bounds = interval(d[:, None], xp=xp)
                    pairs.append(
                        dict(
                            condition=POLICIES[policy],
                            reference=POLICIES[1],
                            metric=metric,
                            k=20,
                            window_days=90,
                            mean=float(d.mean()),
                            ci_low=float(bounds[0, 0]),
                            ci_high=float(bounds[1, 0]),
                            improved=int((d > 1e-12).sum()),
                            worsened=int((d < -1e-12).sum()),
                            tied=int((xp.abs(d) <= 1e-12).sum()),
                            n_evaluated=len(ids),
                        )
                    )
        manifest = saved[0]["manifest"]
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
            policies=POLICIES,
        )
        for row in diverse:
            if row["condition"] in POLICIES[:2]:
                i = POLICIES.index(row["condition"])
                original = next(
                    r
                    for r in results[i]["diversity"]
                    if r["task"] == row["task"] and r["k"] == row["k"]
                )
                expected = original[row["metric"]]
                if (
                    (expected is None) != (row["mean"] is None)
                    or expected is not None
                    and abs(expected - row["mean"]) > 1e-10
                ):
                    raise ValueError("Fixed diversity differs")
        rows.extend(diverse)
        for p in POLICIES:
            for metric in [
                "latency_mean",
                "api_cost",
                "input_tokens",
                "output_tokens",
                "llm_calls",
                "tool_calls",
            ]:
                rows.append(
                    dict(condition=p, aspect="efficiency", task="all", metric=metric, mean=None)
                )
        for t in timings:
            rows.append(
                dict(
                    condition=t["condition"],
                    aspect="efficiency",
                    task="repeat",
                    metric="selection_latency_mean",
                    mean=t["selection_latency_mean"],
                )
            )
        counts = [
            dict(condition=p, target=POLICIES[i], users=int((choices[1, j] == i).sum()))
            for j, p in enumerate(POLICIES)
            for i in (0, 1)
        ]
        for name, data in [
            ("metrics", rows),
            ("paired", pairs),
            ("ild", common),
            ("choices", counts),
            ("timing", timings),
            ("observations", results[0]["observations"]),
        ]:
            write_csv(partial / f"{name}.csv", data)
        figures = render(partial, rows, common, results[0]["observations"])
        verify_references([evidence])
        if source_hash() != processing:
            raise ValueError("Source changed")
        progress("SUCCEEDED: fixed controls, full population and source hashes verified")
    atomic_json(
        partial / "result.json",
        dict(
            status="succeeded",
            phase="validation",
            population=results[0]["population"],
            source_sha256=processing,
            elapsed_seconds=time.perf_counter() - started,
            selection="not_selected",
            external_api="not_run",
            figures=figures,
            artifacts={
                p.name: dict(path=p.name, sha256=file_sha256(p))
                for p in partial.iterdir()
                if p.is_file()
            },
        ),
    )
    os.rename(partial, output)
    return output
