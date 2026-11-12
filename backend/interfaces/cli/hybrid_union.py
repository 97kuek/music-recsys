"""Verify frozen same-union EASE/RRF controls and evaluate ranked Recall strata."""

import argparse
import json
from pathlib import Path

from backend.infrastructure.research.search import load_study_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    config = load_study_config(args.config)
    if config.model != "hybrid":
        parser.error("Hybrid experiment JSON required")
    if not args.run:
        print(
            json.dumps(
                dict(
                    input=str(config.output),
                    output=str(config.output / "union-control"),
                    execution="CUDA; --run required",
                    population="all learning users",
                    control="Exact frozen EASE top-K identity on the shared union",
                ),
                indent=2,
            )
        )
        return
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

    print("GPU preflight: " + require_idle_gpu(), flush=True)
    import cupy as cp

    from backend.infrastructure.research.union_report import run
    from backend.interfaces.notebook.union_figures import figures

    output = run(
        config,
        xp=cp,
        execution=dict(backend="cupy CUDA", cupy_version=cp.__version__, **gpu_metadata()),
        save_figures=figures,
    )
    print(f"Saved: {output}", flush=True)


if __name__ == "__main__":
    main()
