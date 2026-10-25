"""GPU scoring of immutable candidate artifacts with separate future-truth diagnostics."""

import hashlib
import json
import os
import time
from collections import defaultdict
from dataclasses import asdict

import numpy as np

from src.infrastructure.datasets.candidates import candidate_batches
from src.infrastructure.datasets.full_io import DetailWriter, user_rows
from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.evaluation.gpu_metrics import GPUAccuracy, GPUDiversity
from src.infrastructure.recommenders.pipeline import BatchedPipeline
from src.infrastructure.research.metrics import metric_rows
from src.infrastructure.research.store import atomic_json


def describe(values, cp):
    if not values:
        return dict(n=0, mean=None, std=None, quantiles=None)
    values = cp.concatenate([v.ravel() for v in values])
    values = values[cp.isfinite(values)]
    if not len(values):
        return dict(n=0, mean=None, std=None, quantiles=None)
    return dict(
        n=len(values),
        mean=float(values.mean()),
        std=float(values.std()),
        quantiles=cp.asnumpy(
            cp.quantile(values, cp.asarray([0, 0.25, 0.5, 0.75, 0.95, 1]))
        ).tolist(),
    )


class PoolRecall:
    def __init__(self, index, windows, writer):
        self.index, self.windows = index, windows
        self.values = defaultdict(list)
        self.writer = writer

    def add(self, task, sources, pool, observations, users, cp):
        for window in self.windows:
            truths = [
                sorted(
                    {
                        self.index[r[1]]
                        for r in rows
                        if r[2] == window and (r[3] > 0) == (task == "repeat")
                    }
                )
                for rows in observations
            ]
            width = max(1, max(map(len, truths), default=0))
            truth = np.full((len(truths), width), -2, dtype=np.int32)
            for i, ids in enumerate(truths):
                truth[i, : len(ids)] = ids
            truth = cp.asarray(truth)
            counts = cp.asarray(list(map(len, truths)))
            valid = counts > 0

            def recall(ids):
                return cp.any(ids[:, :, None] == truth[:, None, :], axis=1).sum(
                    axis=1
                ) / cp.maximum(counts, 1)

            matrices = (sources[0][:, :100], sources[1][:, :100], pool, sources[0], sources[1])
            values = cp.stack([recall(m) for m in matrices], axis=1)
            if bool((values[:, 2] + 1e-12 < cp.maximum(values[:, 0], values[:, 1])).any()):
                raise ValueError("Union Recall violates set containment")
            self.values[task, window].append(values[valid])
            host_values = cp.asnumpy(values)
            host_valid = cp.asnumpy(valid)
            for i in np.flatnonzero(host_valid):
                self.writer.add(
                    dict(
                        user_id=users[i],
                        task=task,
                        window_days=window,
                        values=host_values[i].tolist(),
                    )
                )

    def finish(self, cp, population):
        rows = []
        labels = ("source_a100", "source_b100", "union100", "source_a200", "source_b200")
        for (task, window), batches in self.values.items():
            values = cp.concatenate(batches)
            n = len(values)
            for i, label in enumerate(labels):
                rows.append(
                    dict(
                        task=task,
                        window_days=window,
                        scope=label,
                        mean=float(values[:, i].mean()) if n else None,
                        n_evaluated=n,
                        n_excluded=population - n,
                    )
                )
        return rows


def evaluate(study, condition, references):
    import cupy as cp
    import pyarrow as pa

    from src.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu
    from src.infrastructure.research.als_report import write_csv

    print("Hybrid evaluation GPU preflight: " + require_idle_gpu(), flush=True)
    cp.get_default_memory_pool().free_all_blocks()
    condition_record = json.loads(json.dumps(asdict(condition)))
    directory = study.output / condition.name
    directory.mkdir(parents=True, exist_ok=False)
    items, plays, item_users = study.catalog()
    index = {item: i for i, item in enumerate(items)}
    engine = BatchedPipeline(study.features(), items, plays, study.spec, xp=cp)
    config = study.config
    accuracy = GPUAccuracy(config.evaluation, index)
    diversity = GPUDiversity(
        study.features(), items, item_users, study.counts["users"], config.evaluation.ks
    )
    score_schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("window_days", pa.int64()),
            ("day_weight", pa.float64()),
            ("metric", pa.string()),
            ("values", pa.list_(pa.float64())),
        ]
    )
    rec_schema = pa.schema(
        [
            ("user_id", pa.string()),
            ("task", pa.string()),
            ("item_ids", pa.list_(pa.string())),
            ("candidate_count", pa.int64()),
            ("pool_count", pa.int64()),
            ("latency_seconds", pa.float64()),
        ]
    )
    details = DetailWriter(directory / "scores.parquet.partial", schema=score_schema)
    recommendations = DetailWriter(directory / "recommendations.parquet.partial", schema=rec_schema)
    pool_details = (
        DetailWriter(
            directory / "pool.parquet.partial",
            schema=pa.schema(
                [
                    ("user_id", pa.string()),
                    ("task", pa.string()),
                    ("window_days", pa.int64()),
                    ("values", pa.list_(pa.float64())),
                ]
            ),
        )
        if len(references) == 2 and condition.mmr_lambda == 1
        else None
    )
    pool_recall = (
        PoolRecall(index, config.evaluation.window_days, pool_details)
        if pool_details is not None
        else None
    )
    observed = iter(user_rows(study.paths["observations"]))
    next_observation = next(observed, None)
    processed = 0
    fusion_seconds = 0.0
    rerank_seconds = 0.0
    pool_hash = hashlib.sha256()
    statistics = defaultdict(list)
    rng = np.random.default_rng(42)
    short = 0
    feature_pinned = 0
    started = time.perf_counter()
    atomic_json(
        directory / "config.json",
        dict(
            signature=study.signature,
            condition=condition_record,
            pipeline=asdict(study.spec),
            source_candidates=[
                dict(directory=r["directory"], sha256=r["artifacts"]["candidates"]["sha256"])
                for r in references
            ],
            execution=dict(
                **gpu_metadata(),
                backend="cupy CUDA",
                metric_dtype="float64",
                batch_users=64,
                seed=condition.seed,
            ),
        ),
    )
    try:
        with (directory / "run.log").open("x") as log:
            for batch in candidate_batches(references, users=64):
                users = [batch[i][0]["user_id"] for i in range(0, len(batch), 2)]
                truths = []
                for user in users:
                    if next_observation is not None and next_observation[0] < user:
                        raise ValueError("Observation outside learning population")
                    truth = (
                        next_observation[1]
                        if next_observation is not None and next_observation[0] == user
                        else []
                    )
                    truths.append(truth)
                    if truth:
                        next_observation = next(observed, None)
                for task_position, task in enumerate(("new", "repeat")):
                    requests = batch[task_position::2]
                    arrays = []
                    for s in range(len(references)):
                        a = np.full((len(users), study.retrieval), -1, dtype=np.int32)
                        for i, request in enumerate(requests):
                            a[i, : len(request[s]["item_indices"])] = request[s]["item_indices"]
                        arrays.append(cp.asarray(a))
                    cp.cuda.Stream.null.synchronize()
                    t = time.perf_counter()
                    if len(arrays) == 1:
                        pool = arrays[0][:, : study.spec.top_m]
                        scores = None
                        counts = (pool >= 0).sum(axis=1)
                    else:
                        pool, scores, counts = engine.fuse(*arrays)
                    cp.cuda.Stream.null.synchronize()
                    fusion_seconds += time.perf_counter() - t
                    for user in users:
                        pool_hash.update((user + "\0" + task + "\0").encode())
                    host_pool = cp.asnumpy(pool)
                    pool_hash.update(host_pool.tobytes())
                    t = time.perf_counter()
                    if scores is None:
                        ranking = cp.full((len(users), 20), -1, dtype=cp.int32)
                        ranking[:, : min(20, pool.shape[1])] = pool[:, :20]
                        trace = {}
                    else:
                        ranking, trace = engine.rerank(pool, scores, lambda_=condition.mmr_lambda)
                    cp.cuda.Stream.null.synchronize()
                    rerank_seconds += time.perf_counter() - t
                    for name, values in trace.items():
                        if name == "pinned_missing":
                            feature_pinned += int(values.sum())
                        else:
                            statistics[name].append(values)
                    if scores is not None:
                        statistics["raw_rrf"].append(cp.where(pool >= 0, scores, cp.nan))
                        statistics["relevance"].append(
                            cp.where(
                                pool >= 0,
                                (study.spec.c + 1) * scores / sum(study.spec.weights),
                                cp.nan,
                            )
                        )
                        # Sixteen deterministic candidate pairs per user; missing/zero features remain missing.
                        sizes = cp.asnumpy(counts)
                        a = np.zeros((len(users), 16), dtype=np.int32)
                        b = a.copy()
                        for i, n in enumerate(sizes):
                            if n >= 2:
                                a[i] = rng.integers(0, int(n), 16)
                                b[i] = (a[i] + 1 + rng.integers(0, int(n) - 1, 16)) % n
                        rows = cp.arange(len(users))[:, None]
                        one = pool[rows, cp.asarray(a)]
                        two = pool[rows, cp.asarray(b)]
                        safe_one, safe_two = cp.maximum(one, 0), cp.maximum(two, 0)
                        valid = (
                            (counts[:, None] >= 2)
                            & engine.features.valid[safe_one]
                            & engine.features.valid[safe_two]
                        )
                        cosine = cp.asarray(
                            engine.features.matrix[safe_one.ravel()]
                            .multiply(engine.features.matrix[safe_two.ravel()])
                            .sum(axis=1)
                        ).reshape(one.shape)
                        statistics["sampled_cosine"].append(
                            cp.where(valid, cp.clip(cosine, 0, 1), cp.nan)
                        )
                    if pool_recall is not None:
                        pool_recall.add(task, arrays, pool, truths, users, cp)
                    eligible = np.asarray([r[0]["candidate_count"] for r in requests])
                    host = cp.asnumpy(ranking)
                    sizes = cp.asnumpy(counts)
                    short += int(((host >= 0).sum(axis=1) < 20).sum())
                    for i, user in enumerate(users):
                        recommendations.add(
                            dict(
                                user_id=user,
                                task=task,
                                item_ids=items[host[i][host[i] >= 0]].tolist(),
                                candidate_count=int(eligible[i]),
                                pool_count=int(sizes[i]),
                                latency_seconds=None,
                            )
                        )
                    for key, (values, valid) in accuracy.add(task, ranking, truths):
                        for i in np.flatnonzero(valid):
                            details.add(
                                dict(
                                    user_id=users[i],
                                    task=key[0],
                                    window_days=key[1],
                                    day_weight=-1.0 if key[2] is None else float(key[2]),
                                    metric=key[3],
                                    values=values[i].tolist(),
                                )
                            )
                    diversity.add(task, ranking, eligible)
                processed += len(users)
                if processed % 8192 < 64:
                    text = f"Evaluated {condition.name}: {processed:,}/{study.counts['users']:,}"
                    print(text, flush=True)
                    log.write(text + "\n")
                    log.flush()
            if processed != study.counts["users"] or next_observation is not None:
                raise ValueError("Incomplete evaluation population")
            rows = accuracy.finish(processed)
            beyond = diversity.finish(
                dict(new=sum(n < processed for n in item_users.values()), repeat=len(items))
            )
            pool_rows = pool_recall.finish(cp, processed) if pool_recall is not None else []
            diagnostic = {name: describe(values, cp) for name, values in statistics.items()}
            log.write("SUCCEEDED: all learning users; source failures were not omitted\n")
    finally:
        details.close()
        recommendations.close()
        if pool_details is not None:
            pool_details.close()
    requests = 2 * processed
    source_rank = sum(r["ranking_seconds"] for r in references)
    inference = fusion_seconds + rerank_seconds
    efficiency = dict(
        model=condition.group,
        training_seconds=sum(r["training_seconds"] for r in references),
        source_ranking_seconds=source_rank,
        source_restore_or_training_seconds=sum(r["model_ready_seconds"] for r in references),
        fusion_seconds=fusion_seconds,
        reranking_seconds=rerank_seconds,
        ranking_seconds=source_rank + inference,
        latency_mean=(source_rank + inference) / requests,
        evaluation_seconds=time.perf_counter() - started,
        generation_calls=requests,
        input_tokens=None,
        output_tokens=None,
        api_cost=None,
        llm_calls=None,
        tool_calls=None,
    )
    study.check()
    artifacts = {}
    for name in ("scores", "recommendations"):
        os.replace(directory / f"{name}.parquet.partial", directory / f"{name}.parquet")
        artifacts[name] = dict(
            path=f"{name}.parquet", sha256=file_sha256(directory / f"{name}.parquet")
        )
    if pool_rows:
        os.replace(directory / "pool.parquet.partial", directory / "pool.parquet")
        artifacts["pool_details"] = dict(
            path="pool.parquet", sha256=file_sha256(directory / "pool.parquet")
        )
        write_csv(directory / "pool.csv", pool_rows)
        artifacts["pool"] = dict(path="pool.csv", sha256=file_sha256(directory / "pool.csv"))
    result = dict(
        format="music-recsys-full-result-v1",
        status="succeeded",
        phase="validation",
        model="hybrid",
        model_parameters={},
        condition=condition_record,
        population=study.counts,
        primary=asdict(config.evaluation),
        accuracy=rows,
        diversity=beyond,
        efficiency=efficiency,
        artifacts=artifacts,
        pool_recall=pool_rows,
        pool_sha256=pool_hash.hexdigest(),
        scale_diagnostics=diagnostic,
        short_lists=short,
        pinned_missing=feature_pinned,
        selection="not_selected",
        figures=[],
        definitions=dict(
            ild="float64 prefix identity; missing/zero/short lists excluded",
            latency="Measured source top-200 retrieval amortization plus fusion/MMR; not single-request latency or a measured top-100 retrieval cost",
            pool="Raw union of each source top-100; candidate_count is global task eligibility, pool_count is actual selection universe",
            mmr="Feature-missing RRF positions pinned; lambda1 identity; valid first choice follows RRF; objective ties within 1e-12 follow RRF",
            uncertainty="Fixed seeds; user bootstrap; seed SD separately; no multiplicity correction",
        ),
    )
    write_csv(
        directory / "metrics.csv",
        list(metric_rows(result)),
        first=("aspect", "task", "window_days", "day_weight", "metric", "k", "mean"),
    )
    atomic_json(directory / "result.json", result)
    print("SUCCEEDED: " + str(directory), flush=True)
    cp.get_default_memory_pool().free_all_blocks()
    return dict(directory=str(directory), **result)
