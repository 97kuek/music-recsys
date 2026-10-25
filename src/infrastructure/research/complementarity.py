"""Bounded, NumPy/CuPy analysis of saved top-20 recommendation complementarity."""

import json
import tempfile
from itertools import combinations, islice, product, zip_longest
from pathlib import Path

import numpy as np

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.evaluation.features import FeaturePrefixes

DEFAULT_SOURCES = {
    "ease-l500": "ease/l500",
    "als-f256-a1-r0.1-s42": "als/sensitivity/f256-e20-a1-r0.1-s42",
    "bpr-f128-e500-s42": "bpr/500epoch/f128-s42",
    "itemcf-cosine-20": "itemcf/cosine/20",
    "bm25-20": "bm25/20",
    "content": "content",
    "popularity-plays": "popularity/plays",
    "popularity-users": "popularity/users",
}


def tie_safe_quartiles(values):
    """Empirical inverse-CDF quartiles: values equal to a cutoff stay together below it."""
    values = np.asarray(values)
    if not len(values):
        raise ValueError("Quartile population must be nonempty")
    boundaries = np.quantile(values, [0.25, 0.5, 0.75], method="inverted_cdf")
    assignments = np.searchsorted(boundaries, values, side="left").astype(np.int32)
    return assignments, dict(
        method="empirical inverse CDF, side=left; equal counts never split; empty quartiles retained",
        boundaries=[int(value) for value in boundaries],
        groups=[dict(stratum=f"Q{i + 1}", count=int((assignments == i).sum())) for i in range(4)],
    )


def load_sources(sources):
    """Verify source artifacts and equality of input, features and evaluation conditions."""
    loaded, common = {}, None
    for name, directory in sources.items():
        directory = Path(directory).resolve()
        result_path, config_path = directory / "result.json", directory / "config.json"
        result, config = (
            json.loads(result_path.read_text(encoding="utf-8")),
            json.loads(config_path.read_text(encoding="utf-8")),
        )
        if result.get("status") != "succeeded" or result.get("phase") != "validation":
            raise ValueError("Only succeeded Validation results may be compared")
        signature = dict(
            manifest=config["manifest"],
            features_sha256=config["features_sha256"],
            population=result["population"],
            evaluation=config["settings"]["evaluation"],
            definitions={
                key: value
                for key, value in result["definitions"].items()
                if key not in {"ranking", "latency"}
            },
        )
        if common is not None and signature != common:
            raise ValueError("Comparison input, population, features or evaluation mismatch")
        common = signature
        reference = result["artifacts"]["recommendations"]
        path = (directory / reference["path"]).resolve()
        if not path.is_relative_to(directory) or file_sha256(path) != reference["sha256"]:
            raise ValueError("Recommendation artifact checksum or path mismatch")
        score_ref = result["artifacts"]["scores"]
        score_path = (directory / score_ref["path"]).resolve()
        if (
            not score_path.is_relative_to(directory)
            or file_sha256(score_path) != score_ref["sha256"]
        ):
            raise ValueError("Score artifact checksum or path mismatch")
        loaded[name] = dict(
            path=path,
            scores=score_path,
            population=result["population"],
            diversity=result["diversity"],
            config=config,
            provenance=dict(
                result=str(result_path),
                result_sha256=file_sha256(result_path),
                config=str(config_path),
                config_sha256=file_sha256(config_path),
                recommendations_sha256=reference["sha256"],
                scores_sha256=score_ref["sha256"],
                source_sha256=config.get("source_sha256"),
                dependency_lock_sha256=config.get("dependency_lock_sha256"),
                execution=config.get("execution"),
            ),
        )
    if len(loaded) < 2:
        raise ValueError("At least two saved recommenders are required")
    return loaded, common


def recommendation_batches(sources, batch_users):
    """Stream identical full user order; reject missing, duplicated or misaligned requests."""
    import duckdb

    def rows(path):
        # CPU results interleave tasks, GPU results emit all new then all repeat in a batch.
        with tempfile.TemporaryDirectory(prefix="music-complementarity-sort-") as temporary:
            with duckdb.connect(
                config={
                    "threads": 1,
                    "memory_limit": "256MB",
                    "temp_directory": temporary,
                }
            ) as connection:
                reader = connection.execute(
                    "SELECT user_id,task,item_ids,candidate_count FROM read_parquet(?) "
                    "ORDER BY user_id,task",
                    [str(path)],
                ).to_arrow_reader(batch_size=2 * batch_users)
                for batch in reader:
                    yield from batch.to_pylist()

    streams = [rows(source["path"]) for source in sources.values()]
    previous = None
    aligned = zip_longest(*streams)
    while batch := list(islice(aligned, 2 * batch_users)):
        if len(batch) % 2:
            raise ValueError("Missing new/repeat request")
        for first, second in zip(batch[::2], batch[1::2], strict=True):
            if any(row is None for row in (*first, *second)):
                raise ValueError("Source population mismatch")
            user = first[0]["user_id"]
            if previous is not None and user <= previous:
                raise ValueError("Users must be unique and sorted")
            for task, request in (("new", first), ("repeat", second)):
                if any((r["user_id"], r["task"]) != (user, task) for r in request):
                    raise ValueError("Saved recommendation users/tasks are misaligned")
                counts = {r["candidate_count"] for r in request}
                if len(counts) != 1:
                    raise ValueError("Source candidate counts differ")
                for row in request:
                    items = row["item_ids"]
                    if len(items) > 20 or len(set(items)) != len(items):
                        raise ValueError("Saved recommendation IDs must be unique and top-20")
                    if len(items) != min(20, row["candidate_count"]):
                        raise ValueError("Saved ranking is shorter than its eligible top-20")
            previous = user
        yield batch


class Complementarity:
    """Accumulate complete pair/K tensors on device; synchronize only at batch output."""

    def __init__(
        self, names, features, items, ks=tuple(range(2, 21)), xp=np, *, popularity_strata=None
    ):
        self.names, self.ks, self.xp = tuple(names), tuple(ks), xp
        self.features = FeaturePrefixes(features, items, xp)
        self.pairs = tuple(combinations(range(len(self.names)), 2))
        self.left = xp.asarray([p[0] for p in self.pairs])
        self.right = xp.asarray([p[1] for p in self.pairs])
        self.positions = xp.asarray(self.ks) - 1
        self.groups = {}
        self.popularity_strata = (
            xp.asarray(popularity_strata) if popularity_strata is not None else None
        )
        self.stratified_pair_positions = xp.asarray(
            [i for i, (left, _) in enumerate(self.pairs) if left == 0]
        )

    def record(
        self,
        task,
        window,
        metric,
        values,
        valid,
        labels,
        *,
        stratum_kind="all",
        stratum="all",
        population_mask=None,
    ):
        xp = self.xp
        if population_mask is not None:
            valid = valid & population_mask[:, None, None]
        valid = xp.broadcast_to(valid, values.shape)
        key = task, window, metric, stratum_kind, stratum
        if key not in self.groups:
            self.groups[key] = [
                xp.zeros(values.shape[1:]),
                xp.zeros(values.shape[1:], dtype=xp.int64),
                0,
                labels,
            ]
        group = self.groups[key]
        group[0] += xp.where(valid, values, 0).sum(axis=0)
        group[1] += valid.sum(axis=0)
        group[2] += len(values) if population_mask is None else population_mask.sum()

    def add(self, task, rankings, truths, *, history_strata=None):
        """rankings[B,M,20]; truths maps window to unique eligible IDs per user."""
        xp = self.xp
        rankings = xp.asarray(rankings)
        batch, models, _ = rankings.shape
        left, right, positions = self.left, self.right, self.positions
        model_labels = [(name, None) for name in self.names]
        pair_labels = [(self.names[m], self.names[n]) for m, n in self.pairs]
        history_strata = xp.asarray(history_strata) if history_strata is not None else None
        # Only cross-model pairs are materialized, rather than M x M including self pairs.
        equal = (rankings[:, left, :, None] == rankings[:, right, None, :]) & (
            rankings[:, left, :, None] >= 0
        )
        overlap = xp.cumsum(xp.cumsum(equal, axis=-1), axis=-2)[:, :, positions, positions]
        ild, ild_valid = self.features.calculate(rankings)
        ild, ild_valid = ild[:, :, positions], ild_valid[:, :, positions]
        common = xp.all(ild_valid, axis=1)[:, None, :]
        self.record(task, None, "common_ild", ild, common, model_labels)
        self.record(task, None, "individual_ild", ild, ild_valid, model_labels)
        pair_valid = ild_valid[:, left] & ild_valid[:, right]
        self.record(task, None, "pair_common_ild_left", ild[:, left], pair_valid, pair_labels)
        self.record(task, None, "pair_common_ild_right", ild[:, right], pair_valid, pair_labels)
        lengths = xp.cumsum(rankings >= 0, axis=2)[:, :, positions]
        union = lengths[:, left] + lengths[:, right] - overlap
        self.record(
            task, None, "overlap_items", overlap, xp.ones_like(overlap, dtype=bool), pair_labels
        )
        self.record(task, None, "jaccard", overlap / xp.maximum(union, 1), union > 0, pair_labels)
        details = {}
        for window, truth in truths.items():
            width = max(1, max(map(len, truth), default=0))
            ids = np.full((batch, width), -2, dtype=np.int32)
            for i, row in enumerate(truth):
                ids[i, : len(row)] = row
            count = xp.asarray([len(row) for row in truth])
            valid = (count > 0)[:, None, None]
            matches = rankings[:, :, :, None] == xp.asarray(ids)[:, None, None, :]
            hit = xp.any(matches, axis=3)
            hits = xp.cumsum(hit, axis=2)[:, :, positions]
            shared = xp.cumsum(xp.cumsum(equal & hit[:, left, :, None], axis=-1), axis=-2)[
                :, :, positions, positions
            ]
            union = hits[:, left] + hits[:, right] - shared
            denominator = xp.maximum(count, 1)[:, None, None]
            for metric, values in {
                "shared_hits": shared,
                "unique_hits_left": hits[:, left] - shared,
                "unique_hits_right": hits[:, right] - shared,
                "union_recall": union / denominator,
                "recall_gain_over_best": (union - xp.maximum(hits[:, left], hits[:, right]))
                / denominator,
            }.items():
                self.record(task, window, metric, values, valid, pair_labels)
            self.record(task, window, "recall", hits / denominator, valid, model_labels)
            # Each unique positive contributes at its earliest rank in any model.
            union_hits = (xp.cumsum(xp.any(matches, axis=1), axis=1) > 0).sum(axis=2)[:, positions]
            values = union_hits / xp.maximum(count, 1)[:, None]
            self.record(
                task, window, "union_recall_all", values[:, None, :], valid, [("all", None)]
            )
            details[window] = self.host(count), self.host(xp.where(valid[:, 0], values, xp.nan))
            self.stratify(
                task,
                window,
                matches,
                equal,
                ids,
                count,
                hits,
                shared,
                values,
                model_labels,
                pair_labels,
                history_strata,
            )
        return details

    def stratify(
        self,
        task,
        window,
        matches,
        equal,
        truth_ids,
        count,
        hits,
        shared,
        all_recall,
        model_labels,
        pair_labels,
        history_strata,
    ):
        xp = self.xp
        selected = self.stratified_pair_positions
        left, right = self.left[selected], self.right[selected]
        labels = [
            label for (left, _), label in zip(self.pairs, pair_labels, strict=True) if left == 0
        ]

        def save(kind, stratum, counts, ranked_hits, shared_hits, union_all, population_mask=None):
            valid = (counts > 0)[:, None, None]
            denominator = xp.maximum(counts, 1)[:, None, None]
            union = ranked_hits[:, left] + ranked_hits[:, right] - shared_hits
            for metric, values in {
                "unique_hits_left": ranked_hits[:, left] - shared_hits,
                "unique_hits_right": ranked_hits[:, right] - shared_hits,
                "union_recall": union / denominator,
                "recall_gain_over_best": (
                    union - xp.maximum(ranked_hits[:, left], ranked_hits[:, right])
                )
                / denominator,
            }.items():
                self.record(
                    task,
                    window,
                    metric,
                    values,
                    valid,
                    labels,
                    stratum_kind=kind,
                    stratum=stratum,
                    population_mask=population_mask,
                )
            self.record(
                task,
                window,
                "recall",
                ranked_hits / denominator,
                valid,
                model_labels,
                stratum_kind=kind,
                stratum=stratum,
                population_mask=population_mask,
            )
            self.record(
                task,
                window,
                "union_recall_all",
                union_all[:, None, :],
                valid,
                [("all", None)],
                stratum_kind=kind,
                stratum=stratum,
                population_mask=population_mask,
            )

        if history_strata is not None:
            for group in range(4):
                save(
                    "history",
                    f"Q{group + 1}",
                    count,
                    hits,
                    shared[:, selected],
                    all_recall,
                    history_strata == group,
                )
        if self.popularity_strata is not None:
            ids = xp.asarray(truth_ids)
            bands = self.popularity_strata[xp.maximum(ids, 0)]
            for group in range(4):
                eligible = (ids >= 0) & (bands == group)
                band_matches = matches & eligible[:, None, None, :]
                hit = xp.any(band_matches, axis=3)
                ranked_hits = xp.cumsum(hit, axis=2)[:, :, self.positions]
                shared_hits = xp.cumsum(
                    xp.cumsum(equal[:, selected] & hit[:, left, :, None], axis=-1), axis=-2
                )[:, :, self.positions, self.positions]
                band_count = eligible.sum(axis=1)
                union_hits = (xp.cumsum(xp.any(band_matches, axis=1), axis=1) > 0).sum(axis=2)
                union_recall = union_hits[:, self.positions] / xp.maximum(band_count, 1)[:, None]
                save(
                    "popularity",
                    f"Q{group + 1}",
                    band_count,
                    ranked_hits,
                    shared_hits,
                    union_recall,
                )

    def host(self, value):
        return np.asarray(value) if self.xp is np else self.xp.asnumpy(value)

    def finish(self):
        rows = []
        for (task, window, metric, kind, stratum), (
            total,
            counts,
            population,
            labels,
        ) in self.groups.items():
            total, counts = self.host(total), self.host(counts)
            population = int(self.host(population).item())
            for condition, (left, right) in enumerate(labels):
                for position, k in enumerate(self.ks):
                    n = int(counts[condition, position])
                    rows.append(
                        dict(
                            task=task,
                            window_days=window,
                            stratum_kind=kind,
                            stratum=stratum,
                            model_left=left,
                            model_right=right,
                            metric=metric,
                            k=k,
                            mean=float(total[condition, position]) / n if n else None,
                            n_evaluated=n,
                            n_excluded=population - n,
                        )
                    )
        return rows


def percentile_interval(values, xp):
    """Linear 2.5/97.5 percentiles, including CUDA arrays with a single column."""
    ordered = xp.sort(values, axis=0)
    positions = xp.asarray([0.025, 0.975], dtype=xp.float64) * (len(values) - 1)
    lower = xp.floor(positions).astype(xp.int64)
    upper = xp.ceil(positions).astype(xp.int64)
    fraction = (positions - lower).reshape((-1,) + (1,) * (values.ndim - 1))
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def paired_score_differences(
    sources,
    ks,
    *,
    windows=(90,),
    xp=np,
    resamples=2000,
    ci_ks=(10, 20),
    seed=42,
    reference=None,
    estimates=None,
):
    """Paired users at all K; a score-path sequence averages fixed seeds per user."""
    import pyarrow.parquet as pq

    names = list(sources)
    if reference is not None and (not names or names[0] != reference):
        raise ValueError("Paired reference must be the first explicit source")
    rows = []
    specifications = (
        ("discovery", "binary_ndcg", -1.0),
        ("discovery", "recall", -1.0),
        ("repeat", "graded_ndcg", 1.0),
        ("repeat", "repeat_recall", -1.0),
    )
    for (task, metric, weight), window in product(specifications, windows):
        users, matrices = None, []
        for source in sources.values():
            paths = source["scores"]
            paths = list(paths) if isinstance(paths, (tuple, list)) else [paths]
            if not paths or len(set(map(str, paths))) != len(paths):
                raise ValueError("Seed score paths must be nonempty and distinct")
            seed_values = []
            for path in paths:
                table = pq.read_table(
                    path,
                    columns=["user_id", "values"],
                    filters=[
                        ("task", "=", task),
                        ("metric", "=", metric),
                        ("window_days", "=", window),
                        ("day_weight", "=", weight),
                    ],
                ).sort_by([("user_id", "ascending")])
                observed_users = table["user_id"].to_pylist()
                if users is not None and users != observed_users:
                    raise ValueError("Paired score users differ across recommenders or seeds")
                if len(set(observed_users)) != len(observed_users):
                    raise ValueError("Duplicate user in paired score condition")
                users = observed_users
                matrix = np.asarray(table["values"].to_pylist(), dtype=float).reshape(-1, len(ks))
                if not np.isfinite(matrix).all():
                    raise ValueError("Saved positive-user scores must be finite")
                seed_values.append(xp.asarray(matrix))
            matrices.append(
                seed_values[0] if len(paths) == 1 else xp.stack(seed_values).mean(axis=0)
            )
        values = xp.stack(matrices, axis=1)
        n = len(values)
        mean = values.mean(axis=0) if n else xp.zeros((len(names), len(ks)))
        positions = [ks.index(k) for k in ci_ks if k in ks]
        sampled = None
        if window == 90 and n >= 2 and positions and resamples >= 2:
            selected = values[:, :, positions]
            sampled = xp.empty((resamples, len(names), len(positions)), dtype=xp.float64)
            rng = np.random.default_rng(seed)
            for start in range(0, resamples, 16):
                size = min(16, resamples - start)
                indices = xp.asarray(rng.integers(0, n, (size, n)))
                sampled[start : start + size] = selected[indices].mean(axis=1)
        if estimates is not None:
            interval = percentile_interval(sampled, xp) if sampled is not None else None
            for m, name in enumerate(names):
                for p, position in enumerate(positions):
                    estimates.append(
                        dict(
                            condition=name,
                            task="new" if task == "discovery" else task,
                            window_days=window,
                            metric=metric,
                            k=ks[position],
                            mean=float(mean[m, position].item()) if n else None,
                            n_evaluated=n,
                            n_excluded=sources[name]["population"]["users"] - n,
                            ci_low=float(interval[0, m, p].item())
                            if interval is not None
                            else None,
                            ci_high=float(interval[1, m, p].item())
                            if interval is not None
                            else None,
                        )
                    )
        for m, other in combinations(range(len(names)), 2):
            if reference is not None and m != 0:
                continue
            ci = None
            if sampled is not None:
                ci = percentile_interval(sampled[:, other] - sampled[:, m], xp)
            for position, k in enumerate(ks):
                ci_position = positions.index(position) if position in positions else None
                rows.append(
                    dict(
                        task="new" if task == "discovery" else task,
                        window_days=window,
                        model_left=names[m],
                        model_right=names[other],
                        metric=f"paired_difference_{metric}",
                        k=k,
                        mean=float((mean[other, position] - mean[m, position]).item())
                        if n
                        else None,
                        n_evaluated=n,
                        n_excluded=sources[names[m]]["population"]["users"] - n,
                        ci_low=float(ci[0, ci_position].item())
                        if ci is not None and ci_position is not None
                        else None,
                        ci_high=float(ci[1, ci_position].item())
                        if ci is not None and ci_position is not None
                        else None,
                    )
                )
    return rows
