"""Read an exported session and publish online metrics without modifying source logs."""

import argparse
import json
from pathlib import Path

from src.domain.study.online_metrics import summarize_online
from src.infrastructure.persistence.private_export import write_private_exclusive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize_online(json.loads(args.input.read_text()))
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    write_private_exclusive(args.output, encoded)


if __name__ == "__main__":
    main()
