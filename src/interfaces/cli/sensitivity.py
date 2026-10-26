"""Sequential Validation parameter diagnostics with immutable trials and cited controls."""

import argparse
import math
from pathlib import Path


def run_study(path, *, minimum_free_gb=5):
    from src.bootstrap import create_sensitivity

    return create_sensitivity().run(path, minimum_free_gb=minimum_free_gb)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--minimum-free-gb", type=float, default=5)
    parser.add_argument("--condition", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.minimum_free_gb) or args.minimum_free_gb < 0:
        parser.error("--minimum-free-gb must be finite and non-negative")
    if args.condition:
        from src.bootstrap import run_sensitivity_condition

        run_sensitivity_condition(args.config, args.condition)
    else:
        print(run_study(args.config, minimum_free_gb=args.minimum_free_gb))


if __name__ == "__main__":
    main()
