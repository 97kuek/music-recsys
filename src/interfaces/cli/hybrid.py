"""Plan or explicitly run the fixed ten-condition Hybrid Validation study."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.application.hybrid import run_study
from src.infrastructure.research.hybrid import HybridStudy, plan
from src.infrastructure.research.search import load_study_config, study_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--source", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = load_study_config(args.config)
    if not args.run and not args.source:
        spec, retrieval, sources, conditions = plan(config)
        print(
            json.dumps(
                dict(
                    pipeline=asdict(spec),
                    retrieval_m=retrieval,
                    sources=sources,
                    conditions=[asdict(c) for c in conditions],
                    selection="not_selected",
                ),
                indent=2,
            )
        )
        return
    from src.interfaces.cli.hybrid_report import publish

    study = HybridStudy(config, report=publish)
    if args.source:
        study.build_source(args.source)
        return
    study.freeze()
    with study_lock(config), (config.output / "run.log").open("a") as log:
        log.write("START fixed Hybrid Validation; Test is not read\n")
        log.flush()
        result = run_study(tuple(study.sources), study.conditions, study)
        log.write("SUCCEEDED: " + result + "\n")
    print(result)


if __name__ == "__main__":
    main()
