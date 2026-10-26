"""Expand the declared grid into deterministic per-condition configurations."""

import itertools
from dataclasses import replace
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


def factor_conditions(config):
    from src.infrastructure.recommenders.factors import integer, number

    required = {"factors", "iterations"}
    optional = {"alpha", "regularization"} if config.model == "als" else set()
    if (
        config.model not in {"als", "bpr"}
        or not required <= set(config.grid)
        or set(config.grid) - required - optional
    ):
        raise ValueError("ALS/BPR study requires factors/iterations and supported sensitivity grid")
    count = len(config.seeds)
    for key, values in config.grid.items():
        if not values:
            raise ValueError("study grid must not be empty")
        count *= len(values)
        for value in values:
            if key in required:
                integer(value, key)
            else:
                number(value, key, positive=key == "regularization")
    if count > config.max_trials:
        raise ValueError("factor study exceeds max_trials")
    keys = ["factors", "iterations", *sorted(set(config.grid) - required)]
    for values in itertools.product(*(config.grid[key] for key in keys), config.seeds):
        parameters, seed = dict(zip(keys, values[:-1], strict=True)), values[-1]
        name = f"f{parameters['factors']}-e{parameters['iterations']}"
        for key, short in (("alpha", "a"), ("regularization", "r")):
            if key in parameters:
                name += f"-{short}{parameters[key]:g}"
        name += f"-s{seed}"
        directory = name
        if (
            config.model == "bpr"
            and len(config.grid["iterations"]) == 1
            and config.output.name == f"{parameters['iterations']}epoch"
        ):
            directory = f"f{parameters['factors']}-s{seed}"
        yield (
            directory,
            replace(
                config,
                name=f"{config.name}-{name}",
                output=config.output / directory,
                parameters={**config.parameters, **parameters},
                grid={},
                seeds=(seed,),
                stage="A",
                max_trials=1,
            ),
        )
