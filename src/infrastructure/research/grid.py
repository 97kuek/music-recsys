"""Expand the declared grid into deterministic per-condition configurations."""

import itertools
from pathlib import Path

from src.infrastructure.research.configuration import load_settings


def grid_variants(config):
    if len(config.seeds) != 1:
        raise ValueError("Grid requires exactly one seed")
    if config.model == "itemcf" and set(config.grid) == {"similarity", "neighbors"}:
        conditions = [
            (f"{similarity}-{neighbors}", dict(similarity=similarity, neighbors=neighbors))
            for similarity, neighbors in itertools.product(
                config.grid["similarity"], config.grid["neighbors"]
            )
        ]
    elif config.model in {"als", "bpr"} and set(config.grid) == {"factors"}:
        from src.infrastructure.recommenders.factors import integer

        conditions = []
        for factors in config.grid["factors"]:
            integer(factors, "factors")
            conditions.append((str(factors), dict(factors=factors)))
    else:
        raise ValueError("Grid requires ItemCF similarity/neighbors or ALS/BPR factors")
    if len(conditions) > config.max_trials:
        raise ValueError("Grid exceeds max_trials")
    for suffix, parameters in conditions:
        raw = load_settings(config.path)
        raw["model"]["parameters"] = {**config.parameters, **parameters}
        raw["search"] = dict(stage="A", seeds=list(config.seeds), max_trials=1)
        # Keep execution identities stable while nesting results by condition.
        itemcf = config.model == "itemcf"
        name = f"itemcf-{suffix}" if itemcf else f"{config.name}-{suffix}"
        relative = (
            Path(parameters["similarity"]) / str(parameters["neighbors"])
            if itemcf
            else Path(suffix)
        )
        raw["experiment"].update(name=name, output=str(config.output / relative))
        yield config.path.parent / config.path.stem / relative.with_suffix(".json"), raw
