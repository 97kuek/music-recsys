"""集約履歴から二値ItemCFを学習 / 曲ブロックごとに共起を計算。"""

import json
import os
import time
from pathlib import Path

import numpy as np


class FullItemCF:
    def __init__(
        self,
        learning_path,
        *,
        neighbors=50,
        similarity="cosine",
        progress=print,
        cache_dir=None,
        use_gpu=False,
    ):
        import duckdb
        import pyarrow.parquet as pq
        from scipy.sparse import csr_matrix

        if type(neighbors) is not int or neighbors < 1:
            raise ValueError("neighbors must be a positive integer")
        if similarity not in {"cosine", "jaccard", "pearson"}:
            raise ValueError("similarity must be cosine, jaccard or pearson")
        if type(use_gpu) is not bool:
            raise ValueError("use_gpu must be boolean")
        from src.infrastructure.models.training import capture_training

        capture_training(self, locals())
        self.neighbors, self.similarity = neighbors, similarity
        if use_gpu:
            from src.infrastructure.recommenders.itemcf_gpu import fit

            fit(self, learning_path, cache_dir=cache_dir, progress=progress)
            return
        from src.infrastructure.datasets.listening_data import file_sha256

        identity = {
            "version": 1,
            "learning_sha256": file_sha256(learning_path),
            "similarity": similarity,
            "neighbors": max(100, neighbors),
        }
        cache = Path(cache_dir) / f"{similarity}-{identity['neighbors']}.npz" if cache_dir else None
        self.cache_hit = False
        if cache is not None and cache.exists():
            with np.load(cache, allow_pickle=False) as stored:
                if json.loads(str(stored["identity"])) != identity:
                    raise ValueError("ItemCF cache input mismatch")
                self.items = stored["items"]
                self.index = {item: j for j, item in enumerate(self.items)}
                self.plays = stored["plays"]
                self.targets = stored["targets"][:, :neighbors]
                self.weights = stored["weights"][:, :neighbors]
                self.training_seconds = float(stored["training_seconds"])
                self.training_provenance["cache_origin"] = {
                    "sha256": file_sha256(cache),
                    "training_provenance": json.loads(str(stored["training_provenance"]))
                    if "training_provenance" in stored
                    else None,
                }
            self.cache_hit = True
            progress(f"Reusing verified ItemCF neighbors: {cache}")
            return
        fit_started = time.perf_counter()
        with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as c:
            stats = c.execute(
                "SELECT item_id,sum(play_count)::BIGINT,count(*) FROM read_parquet(?) GROUP BY item_id ORDER BY item_id",
                [str(learning_path)],
            ).fetchall()
        self.items = np.array([r[0] for r in stats], dtype=object)
        self.index = {item: j for j, item in enumerate(self.items)}
        self.plays = np.array([r[1] for r in stats], dtype=np.int64)
        users_per_item = np.array([r[2] for r in stats], dtype=np.float64)
        parquet = pq.ParquetFile(learning_path)
        indices = np.empty(parquet.metadata.num_rows, dtype=np.int32)
        indptr = [0]
        current, offset = None, 0
        for batch in parquet.iter_batches(batch_size=65536, columns=["user_id", "item_id"]):
            for user, item in zip(
                batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True
            ):
                if user != current:
                    if current is not None:
                        indptr.append(offset)
                    current = user
                indices[offset] = self.index[item]
                offset += 1
        indptr.append(offset)
        matrix = csr_matrix(
            (np.ones(offset, dtype=np.float64), indices, np.array(indptr)),
            shape=(len(indptr) - 1, len(self.items)),
        )
        item_matrix = matrix.T.tocsr()
        width = min(max(100, neighbors), max(0, len(self.items) - 1))
        self.targets = np.zeros((len(self.items), width), dtype=np.int32)
        self.weights = np.zeros((len(self.items), width), dtype=np.float64)
        started = time.perf_counter()
        for start in range(0, len(self.items), 128):
            stop = min(start + 128, len(self.items))
            overlap = item_matrix[start:stop] @ matrix
            for local in range(stop - start):
                item = start + local
                a, b = overlap.indptr[local : local + 2]
                candidates, shared = overlap.indices[a:b], overlap.data[a:b]
                mask = candidates != item
                candidates, shared = candidates[mask], shared[mask]
                denom = (
                    np.sqrt(users_per_item[item] * users_per_item[candidates])
                    if similarity == "cosine"
                    else users_per_item[item] + users_per_item[candidates] - shared
                )
                weights = shared / denom
                if similarity == "pearson":
                    from src.infrastructure.recommenders.binary_similarity import pearson

                    weights = pearson(
                        shared, users_per_item[item], users_per_item[candidates], matrix.shape[0]
                    )
                    positive = weights > 0
                    candidates, weights = candidates[positive], weights[positive]
                order = np.lexsort((candidates, -weights))[:width]
                count = len(order)
                self.targets[item, :count] = candidates[order]
                self.weights[item, :count] = weights[order]
            if start == 0 or stop == len(self.items) or start % 1024 == 0:
                progress(
                    f"ItemCF neighbors {stop:,}/{len(self.items):,} tracks; {time.perf_counter() - started:.1f}s"
                )

        self.training_seconds = time.perf_counter() - fit_started
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(".partial")
            with temporary.open("wb") as stream:
                np.savez_compressed(
                    stream,
                    identity=json.dumps(identity, sort_keys=True),
                    items=self.items.astype(str),
                    plays=self.plays,
                    targets=self.targets,
                    weights=self.weights,
                    training_seconds=self.training_seconds,
                    training_provenance=json.dumps(self.training_provenance, sort_keys=True),
                )
            os.replace(temporary, cache)
        self.targets = self.targets[:, :neighbors]
        self.weights = self.weights[:, :neighbors]

    def rank(self, history, task, top_k=20):
        heard = np.array(sorted(self.index[i] for i in history), dtype=np.int32)
        scores = np.bincount(
            self.targets[heard].ravel(),
            weights=self.weights[heard].ravel(),
            minlength=len(self.items),
        )
        allowed = np.zeros(len(self.items), dtype=bool)
        allowed[heard] = True
        if task == "new":
            allowed = ~allowed
        candidates = np.flatnonzero(allowed)
        order = np.lexsort((candidates, -self.plays[candidates], -scores[candidates]))[:top_k]
        return self.items[candidates[order]].tolist()

    def gpu_score_batch(self, user_ids):
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix

        if self._gpu is None:
            self._gpu = (csr_matrix(self.histories), csr_matrix(self.neighbor_matrix))
        histories, neighbors = self._gpu
        return (histories[cp.asarray(user_ids)] @ neighbors).toarray()
