"""Validationだけで、Popularityのseed変更・反復実行を確認する。"""

import argparse
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

from music_recsys.data import BenchmarkDataset, load_dataset
from music_recsys.evaluation.benchmark import run_benchmark, save_report
from music_recsys.evaluation.tracking import TrackingConfig
from music_recsys.ingestion.types import fingerprint
from music_recsys.recommenders import create_recommender


def repeat_validation(
    dataset: BenchmarkDataset, output: Path, *, seeds: tuple[int, ...], tracking: TrackingConfig
) -> dict:
    if not dataset.provenance or dataset.provenance.get("phase") != "validation":
        raise ValueError("stability study requires validation data")
    if not seeds or any(type(s) is not int or s < 0 for s in seeds):
        raise ValueError("seeds must be non-negative integers")
    rows = []
    for index, seed in enumerate((*seeds, seeds[-1])):
        start = time.perf_counter()
        model = create_recommender("popularity", dataset.train_interactions)
        training_seconds = time.perf_counter() - start
        config = replace(tracking, run_name=f"validation-seed-{seed}-repeat-{index}")
        report = run_benchmark(
            dataset,
            model.recommend,
            model_name="popularity",
            seed=seed,
            training_seconds=training_seconds,
            model_parameters={},
            tracking=config,
        )
        path = save_report(report, output, tracking=config)
        output_sha = fingerprint(
            [
                {"response": asdict(r.response), "evaluation": asdict(r.evaluation)}
                for r in report.runs
            ]
        )
        rows.append(
            {
                "seed": seed,
                "repeat": index,
                "output_sha256": output_sha,
                "ndcg": report.summary.ndcg,
                "recall": report.summary.recall,
                "evaluated": report.summary.n_evaluated,
                "failed": report.summary.n_failed,
                "report_path": str(path / "results.json"),
                "mlflow_run_id": report.tracking.run_id if report.tracking else None,
            }
        )
    deterministic = len({r["output_sha256"] for r in rows}) == 1
    passed = deterministic and all(r["failed"] == 0 and r["evaluated"] > 0 for r in rows)
    return {
        "passed": passed,
        "dataset_sha256": dataset.sha256,
        "provenance": dataset.provenance,
        "model": "popularity",
        "uses_model_randomness": False,
        "deterministic": deterministic,
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repeat validation with seeds; never evaluate test"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 42])
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    result = repeat_validation(
        load_dataset(args.dataset, top_k=args.top_k),
        args.output,
        seeds=tuple(args.seeds),
        tracking=TrackingConfig.from_settings({"enabled": True}),
    )
    path = args.output / "stability.json"
    # Never overwrite a previous study's manifest.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "deterministic": result["deterministic"],
                "passed": result["passed"],
                "runs": len(result["runs"]),
                "summary": str(path),
            }
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
