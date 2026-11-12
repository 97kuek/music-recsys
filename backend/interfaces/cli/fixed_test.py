"""Evaluate or summarize fixed ItemCF/ALS Test conditions through injected adapters."""

import argparse
from pathlib import Path

from backend.bootstrap import finish_fixed_test as finish
from backend.bootstrap import run_fixed_test as run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cpu", action="store_true", help="Explicit CPU reference evaluation")
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Finalize a missing comparison from successful saved Tests",
    )
    args = parser.parse_args()
    print(finish(args.config) if args.summarize else run(args.config, gpu=not args.cpu))


if __name__ == "__main__":
    main()
