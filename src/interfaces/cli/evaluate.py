"""全学習ユーザーの推薦モデルValidation / CLI引数を評価ユースケースへ渡す。"""

import argparse
from pathlib import Path


def run(
    config_path,
    *,
    model_cache=None,
    export_path=None,
    model_version=None,
    phase="validation",
    gpu_popularity=False,
    gpu_itemcf=False,
    als_backend=None,
    ease_memory_budget_mb=None,
):
    from src.application.ports.experiments import EvaluationOptions
    from src.bootstrap import create_evaluation

    return create_evaluation().run(
        config_path,
        EvaluationOptions(
            model_cache=model_cache,
            export_path=export_path,
            model_version=model_version,
            phase=phase,
            gpu_popularity=gpu_popularity,
            gpu_itemcf=gpu_itemcf,
            als_backend=als_backend,
            ease_memory_budget_mb=ease_memory_budget_mb,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--export-model", type=Path)
    parser.add_argument("--model-version")
    parser.add_argument("--phase", choices=("validation", "test"), default="validation")
    parser.add_argument("--gpu-popularity", action="store_true")
    parser.add_argument("--gpu-itemcf", action="store_true")
    parser.add_argument("--als-backend", choices=("cpu", "cuda"))
    parser.add_argument("--ease-memory-budget-mb", type=float)
    args = parser.parse_args()
    print(
        run(
            args.config,
            export_path=args.export_model,
            model_version=args.model_version,
            phase=args.phase,
            gpu_popularity=args.gpu_popularity,
            gpu_itemcf=args.gpu_itemcf,
            als_backend=args.als_backend,
            ease_memory_budget_mb=args.ease_memory_budget_mb,
        )
    )


if __name__ == "__main__":
    main()
