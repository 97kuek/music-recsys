"""Build one verified config/result/CSV index per method without rewriting trial evidence."""

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.models.retention import inspect_model_evidence
from src.infrastructure.research.figure_retention import figure_references
from src.infrastructure.research.metrics import metric_rows
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.store import atomic_json


def collect(directory):
    directory = Path(directory).resolve()
    records, identity = [], None
    for path in sorted(directory.rglob("result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("format") != "music-recsys-full-result-v1":
            continue
        if result.get("phase") == "test":
            continue
        if result.get("status") != "succeeded" or result.get("phase") != "validation":
            raise ValueError("Only completed Validation trials may enter a method catalog")
        config_path = path.with_name("config.json")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        current = dict(
            manifest=config["manifest"],
            features_sha256=config["features_sha256"],
            evaluation=config["settings"]["evaluation"],
            population=result["population"],
        )
        if identity is not None and current != identity:
            raise ValueError("Method inputs, population or evaluation differ")
        if config["settings"]["model"] != directory.name or result["model"] != directory.name:
            raise ValueError("Method directory differs from recorded model")
        if (
            config["population"] != current["population"]
            or result["primary"] != current["evaluation"]
        ):
            raise ValueError("Result/config identity differs")
        identity = current
        verify_artifacts(path.parent, result["artifacts"])
        evidence = dict(
            condition=path.parent.relative_to(directory).as_posix() or ".",
            parameters=config["settings"]["parameters"],
            seeds=config["settings"]["seeds"],
            config=str(config_path),
            config_sha256=file_sha256(config_path),
            result=str(path),
            result_sha256=file_sha256(path),
            source_sha256=config.get("source_sha256"),
            dependency_lock_sha256=config.get("dependency_lock_sha256"),
            model_weights="not_exported",
        )
        if serving := result.get("serving_model"):
            model_path = Path(serving.get("path", "model"))
            if not model_path.is_absolute():
                model_path = path.parent / model_path
            _, evidence["model_weights"] = inspect_model_evidence(
                model_path, expected_manifest_sha256=serving["manifest_sha256"]
            )
            evidence["model_path"] = str(model_path)
            evidence["model_manifest_sha256"] = serving["manifest_sha256"]
            marker = model_path / "retention.json"
            if marker.exists():
                evidence["retention"] = dict(path=str(marker), sha256=file_sha256(marker))
        records.append((evidence, result))
    if not records:
        raise ValueError("No completed full evaluation trials")
    return identity, records


def run(directory):
    directory = Path(directory).resolve()
    output = directory / "summary"
    if output.exists():
        raise FileExistsError("Method summary exists; saved summaries are not overwritten")
    identity, records = collect(directory)
    rows, figures = [], []
    for evidence, result in records:
        metadata = dict(
            condition=evidence["condition"],
            parameters=json.dumps(evidence["parameters"], sort_keys=True),
            seed=evidence["seeds"][0],
            model_weights=evidence["model_weights"],
            result=evidence["result"],
            result_sha256=evidence["result_sha256"],
        )
        rows.extend(dict(**metadata, **row) for row in metric_rows(result))
        base = Path(evidence["result"]).parent
        for reference in figure_references(base, result.get("figures", [])):
            figures.append(
                dict(
                    reference,
                    condition=evidence["condition"],
                    path=os.path.relpath(reference["path"], output),
                )
            )
    order = [
        "condition",
        "parameters",
        "seed",
        "aspect",
        "task",
        "window_days",
        "day_weight",
        "metric",
        "k",
        "mean",
    ]
    order.extend(sorted({key for row in rows for key in row} - set(order)))
    with tempfile.TemporaryDirectory(prefix="catalog-", dir=directory) as temporary:
        stage = Path(temporary) / "report"
        stage.mkdir()
        atomic_json(
            stage / "config.json",
            dict(
                format="music-recsys-method-catalog-config-v1",
                model=directory.name,
                common=identity,
                implementation_sha256=file_sha256(Path(__file__)),
                conditions=[
                    dict(condition=e["condition"], parameters=e["parameters"], seeds=e["seeds"])
                    for e, _ in records
                ],
            ),
        )
        with (stage / "metrics.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=order)
            writer.writeheader()
            writer.writerows(rows)
        atomic_json(
            stage / "result.json",
            dict(
                format="music-recsys-method-catalog-v1",
                status="succeeded",
                phase="validation",
                model=directory.name,
                population=identity["population"],
                evaluation=identity["evaluation"],
                conditions=[e for e, _ in records],
                figures=figures,
                artifacts=dict(
                    metrics=dict(path="metrics.csv", sha256=file_sha256(stage / "metrics.csv"))
                ),
                note="Raw trial configs/results and individual Parquet evidence are preserved; no model selected",
            ),
        )
        (stage / "run.log").write_text(
            f"VERIFIED {len(records)} trials; input/result/artifact hashes checked\n"
        )
        for evidence, _ in records:
            for name in ("config", "result"):
                if file_sha256(evidence[name]) != evidence[name + "_sha256"]:
                    raise ValueError("Source changed before publication")
        output.mkdir()
        os.replace(stage, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", required=True, type=Path)
    args = parser.parse_args()
    print(run(args.method))


if __name__ == "__main__":
    main()
