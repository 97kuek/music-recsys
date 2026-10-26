"""Freeze and evaluate the agreed 37 Validation-backed conditions on final Test."""

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256, read_bundle, validate_bundle
from src.infrastructure.research.full_test import restore, verify_test
from src.infrastructure.research.parameters import condition_name, controls, verify
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.search import load_study_config, study_lock
from src.infrastructure.research.store import atomic_json
from src.interfaces.cli.fixed_test import _reference, _verify_references
from src.paths import PROJECT_ROOT

MODELS = ("ease", "content", "bm25", "bpr")
EXPECTED = dict(ease=3, content=4, bm25=9, bpr=21)
BM25_EXTRA = {(20, 0.3, 0.75), (10, 4.8, 0.75), (100, 4.8, 0.25), (200, 4.8, 0.25)}


def plan():
    """A fixed recipe: never inspect Test scores or choose a primary objective."""
    from src.infrastructure.research.grid import factor_conditions as factors
    from src.infrastructure.research.parameters import conditions

    result = {name: [] for name in MODELS}
    for epoch in (30, 100, 200, 500):
        config = load_study_config(PROJECT_ROOT / f"configs/bpr-{epoch}epoch.json")
        result["bpr"].extend(
            c for _, c in factors(config) if epoch == 500 or c.parameters["factors"] == 128
        )
    config = load_study_config(PROJECT_ROOT / "configs/ease-sensitivity.json")
    result["ease"] = [c for _, c in (*controls(config), *conditions(config))]
    config = load_study_config(PROJECT_ROOT / "configs/content-sensitivity.json")
    result["content"] = [c for _, c in conditions(config)]
    config = load_study_config(PROJECT_ROOT / "configs/bm25-grid.json")
    result["bm25"] = [
        c
        for _, c in conditions(config)
        if (c.parameters["k1"], c.parameters["b"]) == (1.2, 0.75)
        or (c.parameters["neighbors"], c.parameters["k1"], c.parameters["b"]) in BM25_EXTRA
    ]
    if {m: len(c) for m, c in result.items()} != EXPECTED:
        raise ValueError("The agreed 37-condition recipe changed")
    return result


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def baselines(common):
    sources = []
    root = PROJECT_ROOT / "data/experiments"
    paths = [(f"popularity-{n}", root / "popularity" / n / "test") for n in ("plays", "users")]
    paths += [
        (f"itemcf-{s}-{n}", root / "itemcf" / s / str(n) / "test")
        for s in ("cosine", "jaccard")
        for n in (20, 50, 100)
    ]
    paths += [(f"als-{n}", root / "als" / str(n) / "test") for n in (32, 64, 128, 256, 512)]
    for name, directory in paths:
        if not (directory / "result.json").exists():
            continue
        saved, result = _read(directory / "config.json"), _read(directory / "result.json")
        actual = dict(
            manifest=saved["manifest"],
            evaluation=saved["settings"]["evaluation"],
            features_sha256=saved["features_sha256"],
            population=result["population"],
        )
        if actual != common or result["status"] != "succeeded" or result["phase"] != "test":
            raise ValueError("Saved baseline Test differs from the common protocol")
        verify_artifacts(directory, result["artifacts"])
        sources.append(_reference(name, directory, result))
    return sources


def freeze(planned=None, *, ease_memory_budget_mb=24576):
    from src.infrastructure.provenance import source_hash

    planned = plan() if planned is None else planned
    validated, common, protocols = {}, None, {}
    for model, trials in planned.items():
        entries = []
        for expected in trials:
            config = restore(expected.output)
            for field in (
                "model",
                "parameters",
                "seeds",
                "evaluation",
                "validation_dataset",
                "test_dataset",
                "features",
            ):
                if getattr(config, field) != getattr(expected, field):
                    raise ValueError("Recorded Validation differs from the agreed recipe")
            if config.validation_dataset not in validated:
                manifest, _, population = validate_bundle(config.validation_dataset)
                validated[config.validation_dataset] = manifest, population
            result, provenance = verify(
                config, historical=True, validated=validated[config.validation_dataset]
            )
            manifest, _ = read_bundle(config.test_dataset)
            signature = dict(
                manifest=manifest,
                evaluation=json.loads(json.dumps(asdict(config.evaluation))),
                features_sha256=file_sha256(config.features),
            )
            if common is not None and signature != common:
                raise ValueError("Test inputs or evaluation definitions differ")
            common = signature
            name = condition_name(model, config.parameters, config.seeds[0])
            entries.append(
                dict(
                    condition=name,
                    source=_reference(name, config.output, result),
                    validation_provenance=provenance,
                )
            )
        if len({e["condition"] for e in entries}) != len(entries):
            raise ValueError("Repeated condition")
        protocols[model] = dict(
            format="music-recsys-remaining-test-config-v1",
            phase="test",
            model=model,
            gpu=all(c.parameters.get("use_gpu", False) for c in trials),
            conditions=entries,
            common=common,
            source_sha256=source_hash(),
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            ease_memory_budget_mb=ease_memory_budget_mb if model == "ease" else None,
            policy="Conditions fixed before Test; no selection or retuning; full population and all four aspects",
            uncertainty="BPR uses per-user mean across the three fixed seeds for paired user bootstrap; seed SD reported separately; no multiplicity correction",
        )
    # The Test population comes from the bundle metadata validation, not future activity.
    first = next(iter(planned.values()))[0]
    _, _, test_population = validate_bundle(first.test_dataset)
    baseline_common = {**common, "population": test_population}
    references = baselines(baseline_common)
    for model, protocol in protocols.items():
        protocol["common"]["population"] = test_population
        protocol["baselines"] = references
        output = PROJECT_ROOT / "data/experiments" / model / "test"
        target = output / "config.json"
        if target.exists():
            if _read(target) != protocol:
                raise ValueError("Frozen Test protocol changed; never retune saved Test")
        elif output.exists():
            raise FileExistsError("Partial Test evidence exists without a frozen protocol")
    for model, protocol in protocols.items():
        output = PROJECT_ROOT / "data/experiments" / model / "test"
        output.mkdir(parents=True, exist_ok=True)
        if not (output / "config.json").exists():
            atomic_json(output / "config.json", protocol)
    return protocols


def evaluate_condition(model, name):
    from src.infrastructure.provenance import source_hash
    from src.interfaces.cli.evaluate import run

    protocol = _read(PROJECT_ROOT / "data/experiments" / model / "test/config.json")
    if source_hash() != protocol["source_sha256"]:
        raise ValueError("Frozen Test implementation changed")
    entry = next(e for e in protocol["conditions"] if e["condition"] == name)
    _verify_references([entry["source"]])
    config = restore(Path(entry["source"]["config"]).parent)
    output = run(config, phase="test", ease_memory_budget_mb=protocol["ease_memory_budget_mb"])
    verify_test(config, protocol)
    return output


def run_all(*, models=MODELS, minimum_free_gb=2, frozen=False):
    from src.infrastructure.provenance import source_hash

    if not math.isfinite(minimum_free_gb) or minimum_free_gb < 0:
        raise ValueError("minimum_free_gb must be finite and nonnegative")

    if not frozen:
        freeze()
    for model in models:
        output = PROJECT_ROOT / "data/experiments" / model / "test"
        protocol = _read(output / "config.json")
        if source_hash() != protocol["source_sha256"]:
            raise ValueError("Frozen Test implementation changed")
        _verify_references([e["source"] for e in protocol["conditions"]] + protocol["baselines"])
        config = restore(Path(protocol["conditions"][0]["source"]["config"]).parent)
        with (
            study_lock(replace(config, output=output)),
            (output / "run.log").open("a", encoding="utf-8") as log,
        ):
            if (output / "result.json").exists():
                raise FileExistsError("Completed Test comparison already exists")
            for entry in protocol["conditions"]:
                trial = restore(Path(entry["source"]["config"]).parent)
                if (trial.output / "test/result.json").exists():
                    verify_test(trial, protocol)
                    continue
                if (trial.output / "test").exists():
                    raise FileExistsError(
                        "Partial Test condition preserved; inspect before continuing"
                    )
                if shutil.disk_usage(output).free < minimum_free_gb * 1024**3:
                    raise RuntimeError("Insufficient free disk; saved Test evidence is preserved")
                log.write(f"START {entry['condition']}\n")
                log.flush()
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "src.interfaces.cli.final_test",
                        "--model",
                        model,
                        "--condition",
                        entry["condition"],
                    ],
                    check=True,
                )
                log.write(f"SUCCEEDED {entry['condition']}\n")
                log.flush()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "src.interfaces.cli.final_test",
                    "--model",
                    model,
                    "--summarize",
                ],
                check=True,
            )
            log.write("SUCCEEDED: final Test; no model selected\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--frozen", action="store_true")
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--minimum-free-gb", type=float, default=2)
    parser.add_argument("--model", choices=MODELS, help=argparse.SUPPRESS)
    parser.add_argument("--condition", help=argparse.SUPPRESS)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    if args.summarize:
        from src.interfaces.cli.final_report import summarize

        if not args.model:
            parser.error("summarize requires model")
        output = PROJECT_ROOT / "data/experiments" / args.model / "test"
        print(summarize(output, _read(output / "config.json")))
    elif args.condition:
        if not args.model:
            parser.error("condition requires model")
        print(evaluate_condition(args.model, args.condition))
    elif args.plan:
        print(
            json.dumps(
                {
                    m: [
                        dict(
                            condition=condition_name(m, c.parameters, c.seeds[0]),
                            parameters=c.parameters,
                            seed=c.seeds[0],
                            validation=str(c.output),
                        )
                        for c in trials
                    ]
                    for m, trials in plan().items()
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.freeze_only:
        freeze()
    else:
        run_all(models=args.models, minimum_free_gb=args.minimum_free_gb, frozen=args.frozen)


if __name__ == "__main__":
    main()
