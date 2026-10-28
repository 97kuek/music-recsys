"""Read-only Hybrid candidate versions and the fixed comparison plan."""

import json
from dataclasses import asdict
from pathlib import Path

from backend.domain.recommendation.pipeline import FusionSpec, HybridCondition
from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.research.configuration import load_settings


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reference(directory):
    return {
        name: dict(
            path=str(Path(directory) / f"{name}.json"),
            sha256=file_sha256(Path(directory) / f"{name}.json"),
        )
        for name in ("config", "result")
    }


def verify_references(references):
    for reference_ in references:
        for value in reference_.values():
            if file_sha256(value["path"]) != value["sha256"]:
                raise ValueError("Referenced Validation evidence changed")


def plan(config):
    raw = load_settings(config.path)["pipeline"]
    spec = FusionSpec(raw["top_m"], raw["c"], tuple(raw["weights"]), raw["tie_tolerance"])
    if (
        spec.top_m != 100
        or raw["retrieval_m"] != 2 * spec.top_m
        or raw["lambdas"] != [1.0, 0.8, 0.5]
        or raw["bpr_seeds"] != [42, 43, 44]
    ):
        raise ValueError("The agreed fixed initial Hybrid design differs")
    sources = {}
    for model, source in raw["sources"].items():
        for seed in raw["bpr_seeds"] if model == "bpr" else [0]:
            name = f"bpr-s{seed}" if model == "bpr" else model
            directory = (config.path.parent / source["validation"].format(seed=seed)).resolve()
            saved, result = read(directory / "config.json"), read(directory / "result.json")
            if (
                result["phase"] != "validation"
                or result["status"] != "succeeded"
                or result["model"] != model
            ):
                raise ValueError("Candidate version requires a succeeded Validation")
            sources[name] = dict(
                model=model,
                seed=seed,
                directory=str(directory),
                parameters=saved["settings"]["parameters"],
                evidence=reference(directory),
                rationale=source["rationale"],
            )
    if set(sources) != {"ease", "bm25", "content", "bpr-s42", "bpr-s43", "bpr-s44"}:
        raise ValueError("Exactly four model versions and three BPR seeds are required")
    conditions = []
    for name, source in sources.items():
        conditions.append(HybridCondition(name, source["model"], (name,), source["seed"]))
    for pair in (("ease", "bpr"), ("bm25", "content")):
        for seed in raw["bpr_seeds"] if "bpr" in pair else [0]:
            ids = tuple(f"bpr-s{seed}" if s == "bpr" else s for s in pair)
            for lambda_ in raw["lambdas"]:
                group = "-".join(pair) + ("-rrf" if lambda_ == 1 else f"-mmr{lambda_:g}")
                name = group + (f"-s{seed}" if "bpr" in pair else "")
                conditions.append(HybridCondition(name, group, ids, seed, lambda_))
    if len({c.group for c in conditions}) != 10 or len(conditions) != 18:
        raise ValueError("Expected ten comparison groups and eighteen paired-seed runs")
    return spec, raw["retrieval_m"], sources, tuple(conditions)


def matches_plan(config, saved):
    spec, retrieval, sources, conditions = plan(config)

    def plain(value):
        return json.loads(json.dumps(value))

    return (
        saved["pipeline"] == plain(asdict(spec))
        and saved["retrieval_m"] == retrieval
        and saved["sources"] == sources
        and saved["conditions"] == plain([asdict(c) for c in conditions])
    )
