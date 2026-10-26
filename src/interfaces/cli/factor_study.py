"""Sequential, resumable ALS/BPR sensitivity experiments using full Validation."""

import argparse
import math
import time
from pathlib import Path


def run_study(path, *, minimum_free_gb=5):
    from src.bootstrap import create_factor_study

    return create_factor_study().run(path, minimum_free_gb=minimum_free_gb)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--minimum-free-gb", type=float, default=5, help="各条件開始時の最低空きGiB"
    )
    parser.add_argument("--condition", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.minimum_free_gb) or args.minimum_free_gb < 0:
        parser.error("--minimum-free-gb must be finite and non-negative")
    if args.condition:
        from src.bootstrap import run_factor_condition

        run_factor_condition(args.config, args.condition)
    else:
        started = time.perf_counter()
        print(
            run_study(args.config, minimum_free_gb=args.minimum_free_gb),
            f"elapsed={time.perf_counter() - started:.1f}s",
        )


if __name__ == "__main__":
    main()
