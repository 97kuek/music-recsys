"""Run explicit cross-fitted selection between two personal history methods."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu
    from backend.infrastructure.research.history_selection import run
    from backend.infrastructure.research.search import load_study_config
    from backend.interfaces.notebook.personal_figures import figures

    config = load_study_config(args.config)
    if (
        config.model != "history_frequency"
        or config.parameters.get("history_basis") != "recency_log_count"
    ):
        parser.error("Explicit recency-log-count config required")
    print(require_idle_gpu(), flush=True)
    import cupy as cp

    print(
        run(
            config,
            xp=cp,
            execution=dict(backend="cupy CUDA", dtype="float64", seed=42, **gpu_metadata()),
            render=figures,
        )
    )


if __name__ == "__main__":
    main()
