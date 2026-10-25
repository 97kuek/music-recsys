"""Exact binary ItemCF co-occurrence blocks and neighbor prefixes on CUDA."""

import json
import os
import time
from pathlib import Path

import numpy as np


def fit(model, learning_path, *, cache_dir, progress):
    from src.infrastructure.datasets.listening_data import file_sha256
    from src.infrastructure.recommenders.full_factors import FullFactorModel
    from src.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

    progress(f"GPU preflight: {require_idle_gpu()}")
    import cupy as cp
    from cupyx.scipy.sparse import csr_matrix as gpu_csr
    from scipy.sparse import csr_matrix

    started = time.perf_counter()
    matrix = FullFactorModel.load_learning(model, learning_path)
    matrix.data[:] = 1.0
    model.histories = matrix
    identity = dict(
        version=1,
        backend="cupyx float64 binary ItemCF",
        learning_sha256=file_sha256(learning_path),
        similarity=model.similarity,
        neighbors=max(100, model.neighbors),
    )
    cache = (
        Path(cache_dir) / f"{model.similarity}-{identity['neighbors']}-cuda.npz"
        if cache_dir
        else None
    )
    model.cache_hit = False
    if cache is not None and cache.exists():
        with np.load(cache, allow_pickle=False) as saved:
            if (
                json.loads(str(saved["identity"])) != identity
                or not np.array_equal(saved["items"], model.items)
                or not np.array_equal(saved["plays"], model.plays)
            ):
                raise ValueError("ItemCF GPU cache input mismatch")
            model.targets = saved["targets"][:, : model.neighbors]
            model.weights = saved["weights"][:, : model.neighbors]
            model.training_seconds = float(saved["training_seconds"])
            model.execution = json.loads(str(saved["execution"]))
            model.training_provenance["cache_origin"] = dict(
                sha256=file_sha256(cache),
                training_provenance=json.loads(str(saved["training_provenance"])),
            )
        model.cache_hit = True
        progress(f"Reusing verified CUDA ItemCF neighbors: {cache}")
    else:
        histories = gpu_csr(matrix)
        transpose = histories.T.tocsr()
        users_per_item = cp.asarray(histories.sum(axis=0)).ravel()
        width = min(identity["neighbors"], max(0, len(model.items) - 1))
        model.targets = np.zeros((len(model.items), width), dtype=np.int32)
        model.weights = np.zeros((len(model.items), width), dtype=np.float64)
        for start in range(0, len(model.items), 128):
            stop = min(start + 128, len(model.items))
            shared = (transpose[start:stop] @ histories).toarray()
            left = users_per_item[start:stop, None]
            denominator = (
                cp.sqrt(left * users_per_item[None, :])
                if model.similarity == "cosine"
                else left + users_per_item[None, :] - shared
            )
            weights = shared / denominator
            if model.similarity == "pearson":
                from src.infrastructure.recommenders.binary_similarity import pearson

                weights = pearson(shared, left, users_per_item[None, :], matrix.shape[0], xp=cp)
                weights = cp.where(cp.isfinite(weights) & (weights > 0), weights, 0)
            weights[cp.arange(stop - start), cp.arange(start, stop)] = -cp.inf
            selected = cp.argsort(-weights, axis=1, kind="stable")[:, :width]
            values = cp.take_along_axis(weights, selected, axis=1)
            # Match the CPU representation of absent/zero-overlap neighbors.
            model.targets[start:stop] = cp.asnumpy(cp.where(values > 0, selected, 0))
            model.weights[start:stop] = cp.asnumpy(cp.maximum(values, 0))
            if start % 1024 == 0 or stop == len(model.items):
                progress(
                    f"CUDA ItemCF neighbors {stop:,}/{len(model.items):,}; "
                    f"{time.perf_counter() - started:.1f}s"
                )
        cp.cuda.Stream.null.synchronize()
        model.training_seconds = time.perf_counter() - started
        model.execution = dict(
            **gpu_metadata(),
            backend="cupyx sparse binary ItemCF",
            training_device="cuda",
            dtype="float64",
            solver="exact binary co-occurrence; positive cosine/Jaccard/Pearson(phi); stable neighbor sort by item ID",
            block_size=128,
            training_values="binary user-item presence",
            inference_values="binary history",
            random_computation=False,
            bitwise_reproducible=False,
        )
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(".partial")
            with temporary.open("wb") as stream:
                np.savez_compressed(
                    stream,
                    identity=json.dumps(identity, sort_keys=True),
                    items=model.items.astype(str),
                    plays=model.plays,
                    targets=model.targets,
                    weights=model.weights,
                    training_seconds=model.training_seconds,
                    training_provenance=json.dumps(model.training_provenance, sort_keys=True),
                    execution=json.dumps(model.execution, sort_keys=True),
                )
            os.replace(temporary, cache)
        model.targets = model.targets[:, : model.neighbors]
        model.weights = model.weights[:, : model.neighbors]
    width = model.targets.shape[1]
    model.neighbor_matrix = csr_matrix(
        (model.weights.ravel(), model.targets.ravel(), np.arange(len(model.items) + 1) * width),
        shape=(len(model.items), len(model.items)),
        copy=True,
    )
    model.neighbor_matrix.eliminate_zeros()
    model._gpu = None
