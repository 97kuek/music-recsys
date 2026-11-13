"""Measure a few real-input sequence training batches without accuracy evaluation."""

import argparse
from pathlib import Path

from backend.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.search import load_study_config
from backend.infrastructure.research.store import atomic_json
from backend.paths import PROJECT_ROOT


def run(config_path, output, batches=5):
    from backend.infrastructure.recommenders.factors import integer
    from backend.infrastructure.recommenders.full_sequence import FullSequence

    integer(batches, "batches")
    config = load_study_config(config_path)
    if config.model != "esasrec" or config.protocol != "listening-v2" or len(config.seeds) != 1:
        raise ValueError("Explicit listening-v2 esasrec config with one seed required")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    processing = source_hash()
    manifest, paths, _ = validate_bundle(
        config.validation_dataset, memory_limit=f"{config.parameters['input_memory_mb']}MB"
    )
    if manifest["phase"] != "validation":
        raise ValueError("Probe uses training events from the Validation bundle only")
    atomic_json(
        output / "config.json",
        dict(
            mode="resource_probe",
            config=str(config.path),
            parameters=config.parameters,
            batches=batches,
            seed=config.seeds[0],
            source_sha256=processing,
            dependency_lock_sha256=file_sha256(PROJECT_ROOT / "uv.lock"),
            manifest=manifest,
            accuracy="not_measured",
        ),
    )
    with (output / "run.log").open("x") as log:

        def progress(message):
            print(message, flush=True)
            log.write(message + "\n")
            log.flush()

        model = FullSequence(
            paths["learning"],
            **config.parameters,
            seed=config.seeds[0],
            events_path=[str(config.validation_dataset.parent / "source/train.parquet")],
            events_sha256=[manifest["source_files"]["train"]["sha256"]],
            prediction_at=manifest["prediction_at"],
            probe_batches=batches,
            progress=progress,
        )
    if source_hash() != processing:
        raise ValueError("Source changed during resource probe")
    result = dict(
        format="music-recsys-sequence-profile-v1",
        status="succeeded",
        accuracy="not_measured",
        execution=model.execution,
        total_seconds=model.training_seconds,
        source_sha256=processing,
    )
    atomic_json(output / "result.json", result)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batches", type=int, default=5)
    args = parser.parse_args()
    print(run(args.config, args.output, args.batches))


if __name__ == "__main__":
    main()
