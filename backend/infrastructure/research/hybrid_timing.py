"""Full-population, synchronized GPU stage timing on frozen Hybrid candidates."""

import os
import time
from collections import defaultdict
from itertools import islice

import numpy as np

from backend.infrastructure.datasets.candidates import candidate_batches
from backend.infrastructure.datasets.features import load_features
from backend.infrastructure.datasets.listening_data import file_sha256, read_bundle
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import read
from backend.infrastructure.research.results import read_result, verify_artifacts
from backend.infrastructure.research.store import atomic_json
from backend.infrastructure.research.union_verify import records
from backend.paths import PROJECT_ROOT


def rank_union(scores, pool, plays, *, xp):
    """Score descending, training popularity descending, catalog ID ascending."""
    valid = pool >= 0
    safe = xp.maximum(pool, 0)
    values = xp.take_along_axis(scores, safe, axis=1)
    # CuPy lexsort has no batched axis; stable sorts retain the lower-priority ties.
    order = xp.argsort(safe, axis=1, kind="stable")
    for key in (-plays[safe], -xp.where(valid, values, -xp.inf)):
        ordered_key = xp.take_along_axis(key, order, axis=1)
        next_order = xp.argsort(ordered_key, axis=1, kind="stable")
        order = xp.take_along_axis(order, next_order, axis=1)
    return xp.take_along_axis(pool, order, axis=1)[:, :20]


def run(config, *, repetitions=3, save_figures=None):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from backend.domain.recommendation.pipeline import FusionSpec
    from backend.infrastructure.datasets.full_io import DetailWriter
    from backend.infrastructure.recommenders.full_ease import FullEASE
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu
    from backend.infrastructure.recommenders.pipeline import BatchedPipeline

    if type(repetitions) is not int or repetitions < 2:
        raise ValueError("At least two complete timing repetitions required")
    root = config.output
    output, partial = root / "efficiency", root / "efficiency.partial"
    if output.exists() or partial.exists():
        raise FileExistsError("Existing timing outputs must be preserved")
    saved, result = read(root / "config.json"), read_result(config)
    if result["status"] != "succeeded" or result["phase"] != "validation":
        raise ValueError("Successful frozen Validation required")
    manifest, paths = read_bundle(config.validation_dataset)
    if manifest != saved["manifest"]:
        raise ValueError("Input manifest differs")
    evidence = {}

    def record(path):
        evidence[str(path.resolve())] = file_sha256(path)

    for path in (config.path, PROJECT_ROOT / "uv.lock", config.features, *paths.values()):
        record(path)
    if evidence[str(config.features.resolve())] != saved["features_sha256"]:
        raise ValueError("Features differ")
    for directory in (
        root,
        root / "union-control",
        *(root / "candidates" / n for n in ("ease", "bpr-s42", "bpr-s43", "bpr-s44")),
        *(root / c["name"] for c in saved["conditions"] if c["group"].startswith("ease")),
    ):
        document = read(directory / "result.json")
        if document["status"] != "succeeded":
            raise ValueError("Incomplete evidence")
        verify_artifacts(directory, document["artifacts"])
        for name in ("config.json", "result.json"):
            record(directory / name)
        for artifact in document["artifacts"].values():
            record(directory / artifact["path"])
    processing = source_hash()
    print("GPU preflight: " + require_idle_gpu(), flush=True)
    import cupy as cp

    partial.mkdir()
    started = time.perf_counter()
    atomic_json(
        partial / "config.json",
        dict(
            evidence=evidence,
            source_sha256=processing,
            phase="validation",
            selection="not_selected",
            repetitions=repetitions,
            seeds=[42, 43, 44],
            batch_users=64,
            policy="All learning users; cached candidates; no Test; no candidate retrieval timing",
        ),
    )
    with (partial / "run.log").open("x") as log:

        def progress(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        model = FullEASE(
            paths["learning"], **saved["sources"]["ease"]["parameters"], progress=progress
        )
        catalog = pq.read_table(root / "catalog.parquet")
        if catalog["item_id"].to_pylist() != model.items.tolist():
            raise ValueError("Catalog differs")
        features = load_features(
            config.features,
            dataset_id=manifest["dataset_id"],
            snapshot_id=manifest["source"]["snapshot_id"],
        )
        spec = FusionSpec(**{**saved["pipeline"], "weights": tuple(saved["pipeline"]["weights"])})
        engine = BatchedPipeline(features, model.items, model.plays, spec, xp=cp)
        plays = cp.asarray(model.plays)
        users = list(model.users)
        totals = []
        checked = 0
        refit_checked = refit_changed = 0
        reranking_checks = defaultdict(lambda: dict(requests=0, changed_top20_lists=0))
        maximum_score_difference = 0.0
        schema = pa.schema(
            [
                ("repetition", pa.int32()),
                ("seed", pa.int32()),
                ("batch", pa.int32()),
                ("stage", pa.string()),
                ("requests", pa.int32()),
                ("seconds", pa.float64()),
            ]
        )
        writer = DetailWriter(partial / "timings.parquet", schema=schema)
        differences = DetailWriter(
            partial / "ranking_differences.parquet",
            schema=pa.schema(
                [
                    ("repetition", pa.int32()),
                    ("seed", pa.int32()),
                    ("user_id", pa.string()),
                    ("task", pa.string()),
                    ("condition", pa.string()),
                    ("saved_item_ids", pa.list_(pa.string())),
                    ("refit_item_ids", pa.list_(pa.string())),
                    ("candidate_score_max_abs_difference", pa.float64()),
                ]
            ),
        )

        def timed(fn):
            cp.cuda.Stream.null.synchronize()
            start = time.perf_counter()
            value = fn()
            cp.cuda.Stream.null.synchronize()
            return value, time.perf_counter() - start

        try:
            for repetition in range(repetitions):
                for seed in (42, 43, 44):
                    sums = defaultdict(float)
                    names = ["ease", f"bpr-s{seed}"]
                    streams = {
                        name: iter(records(root / name / "recommendations.parquet"))
                        for name in (
                            "ease",
                            f"ease-bpr-rrf-s{seed}",
                            f"ease-bpr-mmr0.8-s{seed}",
                            f"ease-bpr-mmr0.5-s{seed}",
                        )
                    }
                    processed = 0
                    for batch_no, batch in enumerate(
                        candidate_batches([dict(directory=root / "candidates" / n) for n in names])
                    ):
                        batch_users = [r[0]["user_id"] for r in batch[::2]]
                        if batch_users != users[processed : processed + len(batch_users)]:
                            raise ValueError("Full population order differs")
                        expected = {n: list(islice(s, len(batch))) for n, s in streams.items()}
                        user_indices = np.array([model.users[u] for u in batch_users])
                        scores, elapsed = timed(lambda: model.gpu_score_batch(user_indices))

                        def add(stage, seconds, requests):
                            sums[stage] += seconds
                            writer.add(
                                dict(
                                    repetition=repetition,
                                    seed=seed,
                                    batch=batch_no,
                                    stage=stage,
                                    requests=requests,
                                    seconds=seconds,
                                )
                            )

                        add("ease_full_catalog_score", elapsed, 2 * len(batch_users))
                        for pos, task in enumerate(("new", "repeat")):
                            # Compare all saved EASE scores outside the timed stages.
                            ease_rows = [request[0] for request in batch[pos::2]]
                            offsets = np.cumsum([0, *[len(r["item_indices"]) for r in ease_rows]])
                            score_rows = np.repeat(np.arange(len(ease_rows)), np.diff(offsets))
                            score_items = [j for r in ease_rows for j in r["item_indices"]]
                            refit_values = cp.asnumpy(
                                scores[
                                    cp.asarray(score_rows), cp.asarray(score_items, dtype=cp.int32)
                                ]
                            )
                            saved_values = np.array([v for r in ease_rows for v in r["raw_scores"]])
                            score_errors = np.abs(refit_values - saved_values)
                            if not np.isfinite(score_errors).all():
                                raise ValueError("Nonfinite refit candidate scores")
                            maximum_score_difference = max(
                                maximum_score_difference, float(score_errors.max(initial=0))
                            )
                            arrays = []
                            for source in range(2):
                                array = np.full((len(batch_users), 100), -1, dtype=np.int32)
                                for i, request in enumerate(batch[pos::2]):
                                    ids = request[source]["item_indices"][:100]
                                    array[i, : len(ids)] = ids
                                arrays.append(cp.asarray(array))
                            (pool, relevance, _), elapsed = timed(lambda: engine.fuse(*arrays))
                            add("rrf", elapsed, len(batch_users))
                            ranking, elapsed = timed(lambda: rank_union(scores, pool, plays, xp=cp))
                            add("ease_union_sort", elapsed, len(batch_users))
                            rankings = {"ease": ranking, f"ease-bpr-rrf-s{seed}": pool[:, :20]}
                            for weight in (0.8, 0.5):
                                (ranking, _), elapsed = timed(
                                    lambda w=weight: engine.rerank(pool, relevance, lambda_=w)
                                )
                                add(f"mmr{weight}", elapsed, len(batch_users))
                                rankings[f"ease-bpr-mmr{weight}-s{seed}"] = ranking
                            for name, ranking in rankings.items():
                                for i, ids in enumerate(cp.asnumpy(ranking)):
                                    index = pos * len(batch_users) + i
                                    if index >= len(expected[name]):
                                        raise ValueError("Incomplete saved recommendations")
                                    row = expected[name][index]
                                    if (row["user_id"], row["task"]) != (batch_users[i], task):
                                        raise ValueError("Saved recommendation alignment differs")
                                    actual = model.items[ids[ids >= 0]].tolist()
                                    if name == "ease":
                                        refit_checked += 1
                                    else:
                                        reranking_checks[name]["requests"] += 1
                                    if row["item_ids"] != actual:
                                        if name == "ease" or "mmr" in name:
                                            if name == "ease":
                                                refit_changed += 1
                                            else:
                                                reranking_checks[name]["changed_top20_lists"] += 1
                                            differences.add(
                                                dict(
                                                    repetition=repetition,
                                                    seed=seed,
                                                    user_id=batch_users[i],
                                                    task=task,
                                                    condition=name,
                                                    saved_item_ids=row["item_ids"],
                                                    refit_item_ids=actual,
                                                    candidate_score_max_abs_difference=(
                                                        float(
                                                            score_errors[
                                                                offsets[i] : offsets[i + 1]
                                                            ].max(initial=0)
                                                        )
                                                        if name == "ease"
                                                        else None
                                                    ),
                                                )
                                            )
                                            continue
                                        raise ValueError(
                                            f"Refit/reranking differs: {name}/{batch_users[i]}/{task}"
                                        )
                                    checked += 1
                        processed += len(batch_users)
                        if batch_no % 256 == 0:
                            progress(
                                f"Timing repeat={repetition} seed={seed}: {processed}/{len(users)}"
                            )
                    if processed != result["population"]["users"] or any(
                        next(s, None) is not None for s in streams.values()
                    ):
                        raise ValueError("Incomplete population")
                    for stage, seconds in sums.items():
                        totals.append(
                            dict(
                                repetition=repetition,
                                seed=seed,
                                stage=stage,
                                requests=2 * processed,
                                seconds=seconds,
                                seconds_per_request=seconds / (2 * processed),
                            )
                        )
                    progress(f"Completed repeat={repetition} seed={seed}: {dict(sums)}")
        finally:
            writer.close()
            differences.close()
        write_csv(partial / "metrics.csv", totals)
        figures = save_figures(partial, totals) if save_figures is not None else []
        # These retain all four aspects, K sensitivity and measured distributions as evidence.
        for path, digest in evidence.items():
            if file_sha256(path) != digest:
                raise ValueError("Input evidence changed during timing")
        if source_hash() != processing:
            raise ValueError("Processing code changed during timing")
        progress(f"SUCCEEDED: {checked} exact saved Top20 checks")
        progress(f"EASE refit: {refit_changed}/{refit_checked} changed Top20 lists")
        progress(f"RRF/MMR reproduction: {dict(reranking_checks)}")
    artifacts = {p.name: dict(path=p.name, sha256=file_sha256(p)) for p in partial.iterdir()}
    atomic_json(
        partial / "result.json",
        dict(
            format="music-recsys-hybrid-timing-v1",
            status="succeeded",
            phase="validation",
            selection="not_selected",
            population=result["population"],
            artifacts=artifacts,
            execution=dict(
                **gpu_metadata(),
                backend="cupy CUDA",
                score_dtype="float32",
                fusion_dtype="float64",
                training=model.execution,
                repetitions=repetitions,
                seeds=[42, 43, 44],
                synchronization="before and after every timed stage",
            ),
            training_seconds=model.training_seconds,
            elapsed_seconds=time.perf_counter() - started,
            exact_top20_checks=checked,
            refit_comparison=dict(
                requests=refit_checked,
                changed_top20_lists=refit_changed,
                candidate_score_max_abs_difference=maximum_score_difference,
                differences="ranking_differences.parquet",
                scope="Repeated request checks, not unique users; score differences on saved EASE top200",
            ),
            reranking_comparison=dict(reranking_checks),
            figures=figures,
            quality_evidence=str(root / "result.json"),
            observations=result["observations"],
            definitions=dict(
                scope="GPU batch stages on cached candidates; no disk IO, candidate retrieval, or online latency in stage times",
                ease="Actual full-catalog EASE matrix multiplication plus sorting within frozen RRF union; not a candidate-only optimized scorer",
                rrf="Union construction and RRF sorting together; MMR times are additional to RRF",
                repeated="First pass includes cold kernels; later passes are warm, sequential fixed order; ranges are not confidence intervals",
                quality="RRF original Top20 must match every request; EASE refit and MMR rank differences are recorded without asserting numerical equivalence or substituting original quality. Four quality aspects and K2-20 refer to frozen results, not a new quality evaluation of these timing runs",
            ),
            input_tokens=None,
            output_tokens=None,
            api_cost=None,
            llm_calls=None,
            tool_calls=None,
        ),
    )
    os.rename(partial, output)
    return output
