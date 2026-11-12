"""Explicitly prepare train-only selection contexts; API execution is not implicit."""

import argparse
from pathlib import Path

from backend.domain.recommendation.local_selector import TARGETS
from backend.infrastructure.research.search import load_study_config
from backend.infrastructure.research.selection_contexts import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare", action="store_true")
    actions.add_argument("--run-local", action="store_true")
    parser.add_argument("--rule-new", choices=TARGETS)
    parser.add_argument("--rule-repeat", choices=TARGETS)
    args = parser.parse_args()
    config = load_study_config(args.config)
    if config.model != "hybrid":
        parser.error("Hybrid experiment config required")
    if args.run_local:
        if args.rule_new is None or args.rule_repeat is None:
            parser.error("--run-local requires explicit --rule-new and --rule-repeat")
        from backend.infrastructure.research.local_selection import run
        from backend.interfaces.notebook.selector_figures import figures

        print(run(config, new_target=args.rule_new, repeat_target=args.rule_repeat, render=figures))
        return
    if args.rule_new is not None or args.rule_repeat is not None:
        parser.error("Rule targets require --run-local")
    if not args.prepare:
        print("Plan: all learning users, two tasks, completed EASE/RRF/MMR candidates")
        print("Objective, API model, budget and external transmission remain unspecified")
        print("--prepare writes local training aggregates only; it does not execute an LLM")
        print("--run-local compares four fixed policies and an explicit task rule without an API")
        return
    print(prepare(config))


if __name__ == "__main__":
    main()
