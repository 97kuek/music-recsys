"""Run explicit Content/BM25 Validation plans and audit predeclared representative conditions."""

import argparse
import subprocess
import sys
from pathlib import Path

from src.infrastructure.research.search import load_study_config
from src.interfaces.cli.sensitivity import conditions


def audit_sources(content, bm25):
    sources = {}
    trials = list(conditions(bm25))
    for name, trial in trials:
        p = trial.parameters
        if p["k1"] == 1.2 and p["b"] == 0.75:
            sources[f"bm25-{name}"] = trial.output
    for name, trial in conditions(content):
        sources[f"content-{name}"] = trial.output
    for name, trial in trials:
        if trial.parameters == {**bm25.parameters, "b": 0.5}:
            sources[f"bm25-{name}"] = trial.output
    expected = {
        "bm25-n20-k1.2-b0.75",
        "bm25-n10-k1.2-b0.75",
        "bm25-n50-k1.2-b0.75",
        "bm25-n100-k1.2-b0.75",
        "bm25-n200-k1.2-b0.75",
        "bm25-n20-k1.2-b0.5",
        "content-idf1-binary",
        "content-idf1-log_count",
        "content-idf0-binary",
        "content-idf0-log_count",
    }
    if set(sources) != expected or next(iter(sources)) != "bm25-n20-k1.2-b0.75":
        raise ValueError("Expected complete explicit Content/BM25 plans with the baseline first")
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-config", required=True, type=Path)
    parser.add_argument("--bm25-config", required=True, type=Path)
    parser.add_argument("--audit-output", required=True, type=Path)
    args = parser.parse_args()
    content, bm25 = map(load_study_config, (args.content_config, args.bm25_config))
    sources = audit_sources(content, bm25)
    for config in (content, bm25):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "src.interfaces.cli.sensitivity",
                "--config",
                str(config.path),
            ],
            check=True,
        )
    command = [
        sys.executable,
        "-m",
        "src.interfaces.cli.audit",
        "--output",
        str(args.audit_output),
        "--publication",
    ]
    for name, output in sources.items():
        command += ["--source", f"{name}={output}"]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
