"""Explicit sensitivity conditions and verification of saved Validation evidence."""

import json
import math
from dataclasses import asdict, replace

from src.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from src.infrastructure.research.configuration import load_settings
from src.paths import PROJECT_ROOT


def condition_name(model, parameters, seed):
    if model == "ease":
        return f"l{parameters['regularization']:g}"
    if model == "als":
        return (
            f"f{parameters['factors']}-e{parameters['iterations']}"
            f"-a{parameters['alpha']:g}-r{parameters['regularization']:g}-s{seed}"
        )
    if model == "bpr":
        return (
            f"e{parameters['iterations']}-f{parameters['factors']}"
            f"-lr{parameters['learning_rate']:g}-r{parameters['regularization']:g}-s{seed}"
        )
    if model == "bm25":
        return f"n{parameters['neighbors']}-k{parameters['k1']:g}-b{parameters['b']:g}"
    if model == "content":
        return f"idf{int(parameters['use_idf'])}-{parameters['aggregation']}"
    raise ValueError("parameter diagnostics support ease/als/bpr/bm25/content only")


def conditions(config):
    search = load_settings(config.path).get("search", {})
    cases = search.get("conditions")
    if config.protocol != "listening-v2" or config.grid or not isinstance(cases, list) or not cases:
        raise ValueError("parameter sensitivity requires listening-v2 and explicit conditions")
    allowed = {
        "ease": {"regularization"},
        "als": {"alpha", "regularization"},
        "bpr": {"learning_rate", "regularization"},
        "bm25": {"k1", "b", "neighbors"},
        "content": {"use_idf", "aggregation"},
    }.get(config.model, set())
    if not allowed or len(cases) * len(config.seeds) > config.max_trials:
        raise ValueError("unsupported model or conditions exceed max_trials")
    names = set()
    for case in cases:
        if not isinstance(case, dict) or not case or case.keys() - allowed:
            raise ValueError("unsupported sensitivity parameters")
        parameters = {**config.parameters, **case}
        for key, value in case.items():
            if key in {"use_idf", "aggregation"}:
                if (key == "use_idf" and type(value) is not bool) or (
                    key == "aggregation"
                    and (not isinstance(value, str) or value not in {"binary", "log_count"})
                ):
                    raise ValueError("invalid content parameter")
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("parameters must be finite numbers")
            if key == "b":
                valid = 0 <= value <= 1
            elif key == "alpha":
                valid = value >= 0
            else:
                valid = value > 0
            if not valid or (key == "neighbors" and type(value) is not int):
                raise ValueError("parameter outside supported range")
        for seed in config.seeds:
            name = condition_name(config.model, parameters, seed)
            if name in names:
                raise ValueError("duplicate sensitivity condition")
            names.add(name)
            yield (
                name,
                replace(
                    config,
                    name=f"{config.name}-{name}",
                    output=config.output / name,
                    parameters=parameters,
                    grid={},
                    seeds=(seed,),
                    stage="A",
                    max_trials=1,
                ),
            )


def controls(config):
    for entry in load_settings(config.path).get("search", {}).get("controls", []):
        if set(entry) != {"output", "parameters", "seed"}:
            raise ValueError("controls require output, parameters and seed")
        output = (config.path.parent / entry["output"]).resolve()
        if output == config.output or output.is_relative_to(config.output):
            raise ValueError("controls must be existing results outside the new study")
        parameters = {**config.parameters, **entry["parameters"]}
        yield (
            "control-" + condition_name(config.model, parameters, entry["seed"]),
            replace(config, output=output, parameters=parameters, seeds=(entry["seed"],)),
        )


def verify(trial, *, historical=False, validated=None):
    from src.infrastructure.provenance import source_hash

    result = json.loads((trial.output / "result.json").read_text(encoding="utf-8"))
    saved = json.loads((trial.output / "config.json").read_text(encoding="utf-8"))
    if validated is None:
        manifest, _, population = validate_bundle(trial.validation_dataset)
    else:
        manifest, population = validated
    expected = json.loads(json.dumps(asdict(trial), default=str))
    settings = saved["settings"]
    keys = ("model", "parameters", "seeds", "evaluation", "validation_dataset", "features")
    if (
        result.get("status") != "succeeded"
        or result.get("phase") != "validation"
        or manifest["phase"] != "validation"
        or any(settings[key] != expected[key] for key in keys)
        or saved["manifest"] != manifest
        or saved["population"] != population
        or result["population"] != population
        or result["model"] != trial.model
        or result["model_parameters"] != trial.parameters
        or result["primary"] != expected["evaluation"]
        or saved["features_sha256"] != file_sha256(trial.features)
    ):
        raise ValueError("completed condition input/parameters/population changed")
    for key in ("source_sha256", "dependency_lock_sha256"):
        value = saved.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("completed condition provenance hash is invalid")
    lock_hash = file_sha256(PROJECT_ROOT / "uv.lock")
    if not historical and saved["dependency_lock_sha256"] != lock_hash:
        raise ValueError("completed condition dependency lock changed")
    if not historical and (saved["source_sha256"] != source_hash() or settings != expected):
        raise ValueError("completed condition source/settings changed")
    for artifact in result["artifacts"].values():
        path = (trial.output / artifact["path"]).resolve()
        if (
            not path.is_relative_to(trial.output.resolve())
            or file_sha256(path) != artifact["sha256"]
        ):
            raise ValueError("completed condition artifact changed")
    return result, dict(
        output=str(trial.output),
        result_sha256=file_sha256(trial.output / "result.json"),
        config_sha256=file_sha256(trial.output / "config.json"),
        source_sha256=saved["source_sha256"],
        dependency_lock_sha256=saved["dependency_lock_sha256"],
        processing_source_changed=saved["source_sha256"] != source_hash(),
        dependency_lock_changed=saved["dependency_lock_sha256"] != lock_hash,
        execution=saved.get("execution"),
        historical=historical,
    )
