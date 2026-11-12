"""Publish a verified, read-only index of additional Validation diagnostics."""

import argparse
from pathlib import Path

from backend.infrastructure.research.additional_validation import run as run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", action="append", required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run([path for group in args.results for path in group], args.provenance, args.output))


if __name__ == "__main__":
    main()
