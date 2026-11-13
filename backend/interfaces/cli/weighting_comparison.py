"""Compare completed history-weighting runs with explicit saved controls."""

import argparse
from dataclasses import replace
from pathlib import Path

from backend.infrastructure.research.search import load_study_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", action="append", required=True, help="label=directory")
    parser.add_argument("--phase", choices=("validation", "test"), default="validation")
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--comparison-name", choices=("comparison", "history-control"), default="comparison"
    )
    args = parser.parse_args()
    references = {}
    for value in args.reference:
        name, sep, path = value.partition("=")
        if not sep or not name or name in references:
            parser.error("Distinct label=directory references required")
        references[name] = Path(path).resolve()
    config = load_study_config(args.config)
    if args.phase == "test":
        config = replace(config, output=config.output / "test")
    if not (
        config.model in {"transition_rules", "esasrec"}
        or (config.model == "ease" and config.parameters.get("history_weighting") == "log_count")
        or (
            config.model == "history_frequency"
            and config.parameters.get("history_basis")
            in {
                "active_days",
                "last_play",
                "recency_log_count",
                "recency_recent_30d",
                "decayed_count_30d",
            }
        )
    ):
        parser.error("Weighted EASE, temporal history or transition config required")
    if not args.run:
        print(
            f"Plan: explicit saved input-weighting comparison; output {config.output / args.comparison_name}"
        )
        return
    from backend.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

    print(require_idle_gpu(), flush=True)
    import cupy as cp

    from backend.infrastructure.research.weighting_comparison import run
    from backend.interfaces.notebook.personal_figures import figures

    print(
        run(
            config,
            references,
            xp=cp,
            execution=dict(backend="cupy CUDA", dtype="float64", seed=42, **gpu_metadata()),
            render=figures,
            name=args.comparison_name,
            phase=args.phase,
        )
    )


if __name__ == "__main__":
    main()
