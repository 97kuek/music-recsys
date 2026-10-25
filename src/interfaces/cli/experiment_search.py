"""Validation探索・選択・確認・固定・最終TestのCLI入口。"""

import argparse
import json
from pathlib import Path

from src.infrastructure.research.search import (
    freeze_selection,
    load_study_config,
    review_selection,
    run_final_test,
    run_search,
    select_best,
    smoke,
    study_lock,
)


def main():
    parser = argparse.ArgumentParser(
        description="Validation exploration / selection / locked final Test"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "action", choices=("smoke", "search", "select", "review", "freeze", "test", "status")
    )
    parser.add_argument("--accept", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    try:
        from src.interfaces.notebook.search_analysis import workflow_state

        config = load_study_config(args.config)
        actions = {
            "smoke": smoke,
            "search": run_search,
            "select": select_best,
            "freeze": freeze_selection,
            "test": run_final_test,
            "status": workflow_state,
        }
        result = (
            review_selection(config, accepted=args.accept, note=args.note)
            if args.action == "review"
            else actions[args.action](config)
        )
        if args.action != "status" and config.protocol == "listening-v2":
            from src.infrastructure.research.store import compact

            with study_lock(config):
                compact(config)
        if (
            args.action == "search"
            and config.protocol == "listening-v2"
            and result.get("status") == "succeeded"
            and len(
                {
                    str(t["parameters"])
                    for t in result["trials"].values()
                    if t["phase"] == "validation"
                }
            )
            == 1
        ):
            from src.interfaces.notebook.evaluation_report import build_report

            build_report(config.path)
        state = workflow_state(config)
        if args.action != "status":
            with (config.output / "run.log").open("a") as log:
                log.write(
                    json.dumps(
                        {"action": args.action, "status": state["status"]}, ensure_ascii=False
                    )
                    + "\n"
                )
        print(
            json.dumps(
                {
                    "action": args.action,
                    "status": (result or {}).get("status", "saved"),
                    "output": str(config.output),
                    "workflow": state,
                },
                ensure_ascii=False,
            )
        )
        return (
            1
            if (result and result.get("status") in {"failed", "incomplete"})
            or state["status"] == "mismatch"
            else 0
        )
    except Exception as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
