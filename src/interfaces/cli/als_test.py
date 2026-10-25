"""Evaluate predeclared ALS factors on final Test with a uniform execution backend."""

import argparse
from pathlib import Path

from src.interfaces.cli.fixed_test import finish, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--extra-configs", nargs="*", default=[], type=Path)
    parser.add_argument("--cpu", action="store_true", help="Explicit CPU reference for all factors")
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    if args.summarize:
        print(finish(args.config, extra=args.extra_configs))
    else:
        print(run(args.config, gpu=not args.cpu, extra=args.extra_configs))


if __name__ == "__main__":
    main()
