"""Explicit exploratory personal-policy comparison on frozen Validation results."""

import argparse
from pathlib import Path

from backend.infrastructure.research.search import load_study_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--include-tree", action="store_true", help="Add a fixed depth-two policy tree comparison"
    )
    args = parser.parse_args()
    config = load_study_config(args.config)
    if config.model != "hybrid":
        parser.error("Hybrid config required")
    if not args.run:
        print(
            "Plan: all users; two-fold Validation; separate NDCG/Recall rules; all K/windows; no API"
        )
        name = "selector-tree" if args.include_tree else "selector-personal"
        print(f"Output: {config.output / name}")
        return
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

    print(require_idle_gpu(), flush=True)
    import cupy as cp

    cp.arange(4).sum().item()  # Check toolkit/kernel availability before reading large inputs.

    from backend.infrastructure.research.personal_selection import run
    from backend.interfaces.notebook.personal_figures import figures

    print(
        run(
            config,
            xp=cp,
            execution=dict(
                backend="cupy CUDA",
                cupy_version=cp.__version__,
                cuda_runtime=cp.cuda.runtime.runtimeGetVersion(),
                **gpu_metadata(),
            ),
            render=figures,
            include_tree=args.include_tree,
        )
    )


if __name__ == "__main__":
    main()
