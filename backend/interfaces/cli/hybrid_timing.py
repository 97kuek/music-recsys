"""Explicit full-population timing of fixed EASE/RRF/MMR Validation paths."""

import argparse
from pathlib import Path

from backend.infrastructure.research.search import load_study_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    config = load_study_config(args.config)
    if config.model != "hybrid":
        parser.error("Hybrid config required")
    if not args.run:
        print("All learning users, three BPR seeds, three synchronized GPU repetitions")
        print(f"Output: {config.output / 'efficiency'}; --run required; Test not read")
        return
    from backend.infrastructure.research.hybrid_timing import run
    from backend.interfaces.notebook.timing_figures import figures

    print(run(config, save_figures=figures))


if __name__ == "__main__":
    main()
