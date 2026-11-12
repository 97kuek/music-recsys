"""Diagnose saved Hybrid candidate Recall by training history and item popularity."""

import argparse
import json
from pathlib import Path

from backend.infrastructure.research.search import load_study_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--run", action="store_true", help="Explicitly analyze saved Validation candidates"
    )
    args = parser.parse_args()
    config = load_study_config(args.config)
    if config.model != "hybrid":
        parser.error("A Hybrid experiment JSON is required")
    if not args.run:
        print(
            json.dumps(
                dict(
                    input=str(config.output),
                    output=str(config.output / "pool-strata"),
                    strata=["learning track count per user", "learning user count per item"],
                    population="all learning users",
                    execution="CUDA; --run required",
                ),
                indent=2,
            )
        )
        return
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

    print("GPU preflight: " + require_idle_gpu(), flush=True)
    import cupy as cp

    from backend.infrastructure.research.pool_report import run
    from backend.interfaces.notebook.pool_figures import figures

    output, rows = run(
        config,
        xp=cp,
        execution=dict(
            backend="cupy CUDA",
            cupy_version=cp.__version__,
            cuda_runtime=cp.cuda.runtime.runtimeGetVersion(),
            **gpu_metadata(),
        ),
        save_figures=figures,
    )
    print(f"Saved: {output}")
    for row in rows:
        if row["window_days"] == 90 and row["metric"] == "recall_difference":
            value = "unmeasured" if row["mean"] is None else f"{row['mean']:.6f}"
            print(
                f"{row['group']} {row['task']} {row['stratum_kind']} {row['stratum']} "
                f"{row['scope']}: {value} (n={row['n_evaluated']}/{row['n_population']})"
            )


if __name__ == "__main__":
    main()
