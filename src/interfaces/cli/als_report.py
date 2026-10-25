"""Complete missing Validation factors and publish a source-verified ALS report."""

import argparse
from dataclasses import replace
from pathlib import Path


def complete_validation(method):
    from src.infrastructure.recommenders.gpu_runtime import require_idle_gpu
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.evaluate import run
    from src.paths import PROJECT_ROOT

    for factors in (32, 64, 128):
        output = method / str(factors) / "validation"
        if (output / "result.json").exists():
            continue  # The report verifies reused evidence before publication
        if output.exists():
            raise FileExistsError(f"Incomplete output is preserved: {output}")
        print("GPU preflight: " + require_idle_gpu(), flush=True)
        source = load_study_config(PROJECT_ROOT / f"configs/als/{factors}.json")
        run(replace(source, output=output), als_backend="cuda")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", type=Path, required=True)
    parser.add_argument("--complete-validation", action="store_true")
    args = parser.parse_args()
    method = args.method.resolve()
    if method.name != "als":
        raise ValueError("Expected the ALS method directory")
    if (method / "paper").exists():
        raise FileExistsError("Published reports are never overwritten")
    if args.complete_validation:
        complete_validation(method)
    from src.infrastructure.research.als_report import publish
    from src.interfaces.cli.catalog import metric_rows
    from src.interfaces.notebook.als_report_figures import save_figures

    print(publish(method, metric_rows=metric_rows, save_figures=save_figures), flush=True)


if __name__ == "__main__":
    main()
