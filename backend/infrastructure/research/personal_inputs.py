"""Read aligned, immutable individual scores without duplicating source artifacts."""

import numpy as np

from backend.infrastructure.research.hybrid_config import read, reference, verify_references
from backend.infrastructure.research.results import verify_artifacts
from backend.infrastructure.research.selection_candidates import bind_candidates


def inputs(root):
    import pyarrow.parquet as pq

    saved, result = read(root / "config.json"), read(root / "result.json")
    candidates, evidence = bind_candidates(root, saved, result)
    context_root = root / "selection-context"
    context_config, context_result = (
        read(context_root / "config.json"),
        read(context_root / "result.json"),
    )
    verify_artifacts(context_root, context_result["artifacts"])
    verify_references([context_config["evidence"]])
    if (
        context_config["candidates"] != candidates
        or context_result["users"] != result["population"]["users"]
    ):
        raise ValueError("Context provenance differs")
    evidence.update(context_config["evidence"])
    evidence.update({f"context/{k}": v for k, v in reference(context_root).items()})
    for name, artifact in context_result["artifacts"].items():
        evidence[f"context/{name}"] = dict(
            path=str(context_root / artifact["path"]), sha256=artifact["sha256"]
        )
    contexts = pq.read_table(context_root / "contexts.parquet").to_pandas()
    new, repeat = (contexts[contexts.task == t].reset_index(drop=True) for t in ("new", "repeat"))
    users = new.user_id.tolist()
    if (
        users != sorted(set(users))
        or users != repeat.user_id.tolist()
        or len(users) != context_result["users"]
    ):
        raise ValueError("Full paired user population required")
    return saved, result, candidates, evidence, users, (new, repeat)


def score_blocks(candidates, users, ks, progress):
    import pyarrow.parquet as pq

    lookup = {u: i for i, u in enumerate(users)}
    blocks = {}
    keys = ["task", "window_days", "day_weight", "metric"]
    for model, (target, candidate) in enumerate(candidates.items()):
        for seed_index, run in enumerate(candidate["runs"]):
            table = pq.read_table(run["artifacts"]["scores"]["path"])
            meta = table.select(["user_id", *keys]).to_pandas()
            flat = table["values"].combine_chunks()
            lengths = np.diff(flat.offsets.to_numpy())
            if not np.all(lengths == len(ks)):
                raise ValueError("Score K shape differs")
            values = flat.values.to_numpy().reshape(-1, len(ks))
            if not np.isfinite(values).all():
                raise ValueError("Invalid saved score")
            seen = set()
            for key, positions in meta.groupby(keys, sort=True).indices.items():
                seen.add(key)
                ids = meta.iloc[positions].user_id.tolist()
                if len(set(ids)) != len(ids):
                    raise ValueError("Duplicate score user")
                indices = np.array([lookup[u] for u in ids])
                if model == 0 and seed_index == 0:
                    blocks[key] = (indices, np.zeros((len(ids), 4, len(ks)), dtype=np.float64))
                if key not in blocks or not np.array_equal(indices, blocks[key][0]):
                    raise ValueError("Score users differ between candidates or seeds")
                blocks[key][1][:, model] += values[positions] / len(candidate["runs"])
            if seen != set(blocks):
                raise ValueError("Score conditions differ")
            progress(f"Read aligned scores: {target} seed {run['condition']['seed']}")
    return blocks
