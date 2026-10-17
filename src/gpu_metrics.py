"""Batched listening-v2 accuracy and diversity calculations on CUDA."""

from collections import defaultdict

import numpy as np


class GPUAccuracy:
    def __init__(self, config, item_index):
        import cupy as cp

        self.cp, self.config, self.index = cp, config, item_index
        self.discounts = 1 / cp.log2(cp.arange(2, 22, dtype=cp.float64))
        self.positions = cp.asarray(config.ks) - 1
        self.groups = defaultdict(lambda: dict(total=cp.zeros(len(config.ks)), n=0, primary=[]))

    def add(self, task, rankings, observations):
        cp, config = self.cp, self.config
        batch = len(observations)
        for window in config.window_days:
            truth = [
                [
                    (self.index[r[1]], r[5], r[6])
                    for r in rows
                    if r[2] == window and (r[3] > 0) == (task == "repeat")
                ]
                for rows in observations
            ]
            width = max(20, max(map(len, truth), default=0))
            ids = np.full((batch, width), -2, dtype=np.int32)
            plays = np.zeros((batch, width))
            days = np.zeros_like(plays)
            for row, values in enumerate(truth):
                if values:
                    array = np.asarray(values)
                    ids[row, : len(values)] = array[:, 0]
                    plays[row, : len(values)] = array[:, 1]
                    days[row, : len(values)] = array[:, 2]
            ids = cp.asarray(ids)
            counts = cp.asarray([len(t) for t in truth])
            valid = counts > 0
            matches = rankings[:, :, None] == ids[:, None, :]
            binary = cp.any(matches, axis=2)
            hits = cp.cumsum(binary, axis=1)
            dcg = cp.cumsum(binary * self.discounts, axis=1)
            ideal = cp.cumsum((cp.arange(20)[None, :] < counts[:, None]) * self.discounts, axis=1)
            label = "discovery" if task == "new" else "repeat"
            if task == "new":
                key = (label, window, None, "binary_ndcg")
                yield key, self._record(key, dcg / cp.maximum(ideal, 1e-300), valid)
            key = (label, window, None, "recall" if task == "new" else "repeat_recall")
            yield key, self._record(key, hits / cp.maximum(counts[:, None], 1), valid)
            log_plays, log_days = cp.log2(1 + cp.asarray(plays)), cp.log2(1 + cp.asarray(days))
            match_weights = matches.astype(cp.float64)
            ranked_plays = cp.einsum("bkt,bt->bk", match_weights, log_plays)
            ranked_days = cp.einsum("bkt,bt->bk", match_weights, log_days)
            for weight in config.day_weights:
                gains = log_plays + weight * log_days
                ideal_gains = cp.sort(gains, axis=1)[:, ::-1][:, :20]
                denominator = cp.cumsum(ideal_gains * self.discounts, axis=1)
                numerator = cp.cumsum(
                    (ranked_plays + weight * ranked_days) * self.discounts, axis=1
                )
                key = (
                    "retention_proxy" if task == "new" else "repeat",
                    window,
                    weight,
                    "graded_ndcg",
                )
                yield key, self._record(key, numerator / cp.maximum(denominator, 1e-300), valid)

    def _record(self, key, values, valid):
        values = values[:, self.positions]
        group = self.groups[key]
        selected = values[valid]
        group["total"] += selected.sum(axis=0)
        group["n"] += len(selected)
        if key[1] == self.config.primary_window_days and key[2] in (
            None,
            self.config.primary_day_weight,
        ):
            group["primary"].append(selected)
        return self.cp.asnumpy(values), self.cp.asnumpy(valid)

    def finish(self, population):
        cp = self.cp
        rows = []
        for key, group in self.groups.items():
            ci = None
            if group["primary"] and group["n"]:
                values = cp.concatenate(group["primary"])
                means = cp.empty((2000, len(self.config.ks)), dtype=cp.float64)
                rng = np.random.default_rng(42)
                for start in range(0, 2000, 32):
                    size = min(32, 2000 - start)
                    indices = cp.asarray(rng.integers(0, len(values), (size, len(values))))
                    means[start : start + size] = values[indices].mean(axis=1)
                ci = cp.asnumpy(cp.quantile(means, cp.asarray([0.025, 0.975]), axis=0))
            mean = cp.asnumpy(group["total"] / max(1, group["n"]))
            for index, k in enumerate(self.config.ks):
                rows.append(
                    dict(
                        task=key[0],
                        window_days=key[1],
                        day_weight=key[2],
                        metric=key[3],
                        k=k,
                        mean=float(mean[index]) if group["n"] else None,
                        n_evaluated=group["n"],
                        n_excluded=population - group["n"],
                        ci_low=float(ci[0, index]) if ci is not None else None,
                        ci_high=float(ci[1, index]) if ci is not None else None,
                    )
                )
        return rows


class GPUDiversity:
    def __init__(self, features, items, item_users, n_users, ks):
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix
        from scipy.sparse import coo_matrix

        from src.features import unit_vector

        self.cp, self.ks = cp, ks
        tags = {tag: i for i, tag in enumerate(sorted({t for v in features.values() for t in v}))}
        rows, cols, data, present, nonzero = [], [], [], [], []
        for index, item in enumerate(items):
            vector = unit_vector(features.get(item, {}))
            present.append(item in features)
            nonzero.append(bool(vector))
            for tag, value in vector.items():
                rows.append(index)
                cols.append(tags[tag])
                data.append(value)
        matrix = coo_matrix((data, (rows, cols)), shape=(len(items), max(1, len(tags)))).tocsr()
        self.features = csr_matrix(matrix)
        self.present, self.nonzero = cp.asarray(present), cp.asarray(nonzero)
        self.novelty = -cp.log2(cp.asarray([(item_users[i] + 1) / (n_users + 2) for i in items]))
        self.groups = defaultdict(
            lambda: dict(
                count=0,
                ild_sum=cp.array(0.0),
                ild_count=cp.array(0),
                novelty_sum=cp.array(0.0),
                novelty_count=cp.array(0),
                exposed=cp.zeros(len(items), dtype=bool),
                reasons=defaultdict(lambda: cp.array(0)),
            )
        )

    def add(self, task, rankings, candidate_counts):
        cp = self.cp
        valid = rankings >= 0
        lengths = valid.sum(axis=1)
        safe = cp.maximum(rankings, 0)
        unique = cp.unique(safe)
        vectors = self.features[unique]
        gram = (vectors @ vectors.T).toarray()
        indices = cp.searchsorted(unique, safe)
        distances = 1 - cp.clip(gram[indices[:, :, None], indices[:, None, :]], 0, 1)
        pair_prefix = cp.cumsum(cp.triu(distances, k=1).sum(axis=1), axis=1)
        missing = cp.cumsum(~self.present[safe], axis=1)
        zero = cp.cumsum(self.present[safe] & ~self.nonzero[safe], axis=1)
        novelty = cp.cumsum(self.novelty[safe] * valid, axis=1)
        row_ids = cp.arange(len(rankings))
        candidate_counts = cp.asarray(candidate_counts)
        for k in self.ks:
            group = self.groups[task, k]
            group["count"] += len(rankings)
            n = cp.minimum(lengths, k)
            last = cp.maximum(n - 1, 0)
            has_missing = missing[row_ids, last] > 0
            has_zero = zero[row_ids, last] > 0
            ild_valid = (n >= 2) & ~has_missing & ~has_zero
            group["ild_sum"] += cp.sum(
                cp.where(ild_valid, pair_prefix[row_ids, last] / cp.maximum(n * (n - 1) / 2, 1), 0)
            )
            group["ild_count"] += ild_valid.sum()
            group["novelty_sum"] += cp.sum(
                cp.where(n > 0, novelty[row_ids, last] / cp.maximum(n, 1), 0)
            )
            group["novelty_count"] += (n > 0).sum()
            ranked = rankings[:, :k]
            group["exposed"][ranked[ranked >= 0]] = True
            group["reasons"]["empty_candidates"] += (candidate_counts == 0).sum()
            group["reasons"]["insufficient_valid_items"] += ((candidate_counts > 0) & (n < 2)).sum()
            group["reasons"]["features_missing"] += ((n >= 2) & has_missing).sum()
            group["reasons"]["features_zero_norm"] += ((n >= 2) & has_zero).sum()

    def finish(self, universe_sizes):
        result = []
        for (task, k), group in sorted(self.groups.items()):
            size = universe_sizes[task]
            exposed = int(group["exposed"].sum().item())
            row = dict(
                task=task,
                k=k,
                candidate_items=size,
                exposed_items=exposed,
                catalog_coverage=exposed / size if size else None,
                n_requests=group["count"],
                ild_exclusion_counts={
                    key: int(value.item())
                    for key, value in group["reasons"].items()
                    if value.item()
                },
            )
            for metric in ("ild", "novelty"):
                count = int(group[f"{metric}_count"].item())
                row[metric] = float(group[f"{metric}_sum"].item()) / count if count else None
                row[f"{metric}_evaluated"] = count
                row[f"{metric}_excluded"] = group["count"] - count
            result.append(row)
        return result
