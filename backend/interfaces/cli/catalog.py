"""Build one verified config/result/CSV index per method without rewriting trial evidence."""

import argparse
from pathlib import Path

from backend.infrastructure.research.catalog import collect as collect
from backend.infrastructure.research.catalog import run as run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", required=True, type=Path)
    args = parser.parse_args()
    print(run(args.method))


if __name__ == "__main__":
    main()
