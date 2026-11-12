"""Inspect or package a verified portable serving bundle from an explicit configuration."""

import argparse
import json
from pathlib import Path

from backend.bootstrap import package_bundle as package
from backend.infrastructure.models.bundle import inventory as inventory
from backend.infrastructure.models.bundle import verify as verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--study", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="容量とhashを確認、コピーしない")
    parser.add_argument("--max-bytes", type=int, help="生成JSONを含むbundleの容量上限")
    args = parser.parse_args()
    if args.max_bytes is not None and args.max_bytes <= 0:
        parser.error("--max-bytes must be positive")
    if args.dry_run:
        report = inventory(args.config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.max_bytes is not None and report["payload_bytes"] > args.max_bytes:
            parser.exit(1, "bundle payload exceeds --max-bytes\n")
    elif args.output is None:
        parser.error("--output is required unless --dry-run is used")
    else:
        print(package(args.config, args.output, study_path=args.study, max_bytes=args.max_bytes))


if __name__ == "__main__":
    main()
