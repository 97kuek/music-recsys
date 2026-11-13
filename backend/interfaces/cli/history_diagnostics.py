"""Diagnose paired history-method scores, without fitting selection rules."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu
    from backend.infrastructure.research.history_diagnostics import run
    from backend.interfaces.notebook.history_diagnostics_figures import figures

    print(require_idle_gpu(), flush=True)
    import cupy as cp

    print(
        run(
            args.root.resolve(),
            xp=cp,
            render=figures,
            execution=dict(backend="cupy CUDA", dtype="float64", seed=42, **gpu_metadata()),
        )
    )


if __name__ == "__main__":
    main()
