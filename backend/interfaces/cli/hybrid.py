"""Plan or explicitly run the fixed ten-condition Hybrid Validation study."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from backend.application.hybrid import run_study
from backend.infrastructure.research.hybrid import HybridStudy
from backend.infrastructure.research.hybrid_config import plan
from backend.infrastructure.research.search import load_study_config, study_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--phase", choices=("validation", "test"), default="validation")
    parser.add_argument("--ease-memory-budget-mb", type=float)
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
                    phase=args.phase,
                ),
                indent=2,
            )
        )
        return
    from backend.bootstrap import publish_hybrid_report as publish

    study = HybridStudy(
        config, report=publish, phase=args.phase, ease_memory_budget_mb=args.ease_memory_budget_mb
    )
    if args.source:
        study.build_source(args.source)
        return
    study.freeze()
    with study_lock(study.config), (study.output / "run.log").open("a") as log:
        log.write(f"START fixed Hybrid {study.phase}; all conditions fixed before scoring\n")
        log.flush()
        result = run_study(tuple(study.sources), study.conditions, study)
        log.write("SUCCEEDED: " + result + "\n")
    print(result)


if __name__ == "__main__":
    main()
