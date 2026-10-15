"""探索のフェーズ制御から、入力形式・Evaluator・表示用変換を分離する。"""

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path

from music_recsys.data import load_dataset
from music_recsys.evaluation.tracking import TrackingConfig
from music_recsys.ingestion.types import canonical_json

V2_OBJECTIVES = {
    "discovery": {"binary_ndcg", "recall"},
    "retention_proxy": {"graded_ndcg"},
    "repeat": {"graded_ndcg", "repeat_recall"},
}


def load_data(config, path):
    if config.protocol == "listening-v2":
        return _listening_data(
            str(Path(path).resolve()), config.evaluation, data_fingerprint(config, path)
        )
    return load_dataset(path, top_k=20)


@lru_cache(maxsize=1)
def _listening_data(path, evaluation, content_sha256):
    from music_recsys.listening_data import load_listening_dataset

    data = load_listening_dataset(path, evaluation)
    if data.sha256 != content_sha256:
        raise ValueError("bundle changed while loading")
    return data


def data_fingerprint(config, path):
    if config.protocol == "listening-v2":
        from music_recsys.listening_data import read_bundle

        manifest, _ = read_bundle(path)
        return hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_test_training(config, test, validation):
    """v2の元分割・学習事実を照合する。Testの正解は設定選択に使用しない。"""
    import duckdb

    from music_recsys.listening_data import read_bundle

    a, b = validation.manifest, test.manifest
    if b["temporal"] != a["temporal"]:
        raise ValueError("比較条件不一致: Testの分割境界")
    for role in ("train", "validation"):
        if (
            not a.get("source_files", {}).get(role)
            or b.get("source_files", {}).get(role) != a["source_files"][role]
        ):
            raise ValueError("比較条件不一致: Testのsource出典")
    if set(b.get("source_files", {})) != {"train", "validation", "test"}:
        raise ValueError("TestはTrain + Validationのsourceが必要です")
    if len(test.train_interactions) != sum(
        b["source_files"][role]["row_count"] for role in ("train", "validation")
    ):
        raise ValueError("Test learning event count must include all Train + Validation")
    # 各累積窓の観測は重ねて加算せず、同じ組の最大回数を下限とする。
    _, paths = read_bundle(config.validation_dataset)
    before = Counter((x.user_id, x.item_id) for x in validation.train_interactions)
    after = Counter((x.user_id, x.item_id) for x in test.train_interactions)
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
        cursor = connection.execute(
            "SELECT user_id,item_id,max(evaluation_play_count) FROM read_parquet(?) GROUP BY user_id,item_id",
            [str(paths["observations"])],
        )
        while rows := cursor.fetchmany(65536):
            for user, item, count in rows:
                before[(user, item)] += count
    if any(after[key] < count for key, count in before.items()):
        raise ValueError("Test学習履歴にTrain + Validationの観測が反映されていません")


@dataclass(frozen=True)
class TrialEvaluation:
    report: object
    curves: dict
    valid: bool
    error_type: str | None
    metadata: dict
    summary: object


def evaluate_trial(
    config, data, model, parameters, seed, training_seconds, config_sha256, features, binary_runner
):
    if config.protocol == "listening-v2":
        from music_recsys.evaluation.listening_benchmark import run_listening_benchmark

        report = run_listening_benchmark(
            data,
            model.recommend,
            config=config.evaluation,
            model_name=config.model,
            seed=seed,
            training_seconds=training_seconds,
            model_parameters=parameters,
            config_sha256=config_sha256,
            tracking=TrackingConfig(),
            features=features,
        )
        curves = listening_curves(config, data, report)
        primary = next(row for row in curves["metrics"] if row["k"] == config.primary_k)
        invalid = any(
            d["invalid_count"] or d["duplicate_count"] or d["overflow_count"]
            for r in report.runs
            for d in r["diagnostics"]
        )
        error = next(
            (r["response"]["error_type"] for r in report.runs if r["response"]["error_type"]), None
        )
        if invalid:
            error = error or "InvalidModelOutput"
        if not primary["n_evaluated"]:
            error = error or "NoEvaluablePrimaryUsers"
        return TrialEvaluation(
            report,
            curves,
            not report.metadata["n_failed"] and not error,
            error,
            report.metadata,
            report.summary,
        )
    from music_recsys.search_metrics import ranking_metrics

    report = binary_runner(
        data,
        model.recommend,
        model_name=config.model,
        seed=seed,
        training_seconds=training_seconds,
        model_parameters=parameters,
        config_sha256=config_sha256,
        tracking=TrackingConfig(),
    )
    public = {
        k: v for k, v in report.metadata.evaluation_conditions.items() if k != "dataset_provenance"
    }
    report = replace(report, metadata=replace(report.metadata, evaluation_conditions=public))
    curves = ranking_metrics(data, report, features=features)
    valid = (
        not report.summary.n_failed
        and bool(report.summary.n_evaluated)
        and not any(r.evaluation.invalid_count or r.evaluation.duplicate_count for r in report.runs)
    )
    error = (
        next(
            (r.response.error_type for r in report.runs if r.response.error_type),
            "InvalidModelOutput",
        )
        if not valid
        else None
    )
    return TrialEvaluation(
        report, curves, valid, error, asdict(report.metadata), asdict(report.summary)
    )


def listening_curves(config, data, report):
    """全条件はlong形式で保持し、探索表は事前固定した主条件だけを投影する。"""
    evaluation = config.evaluation
    task = config.task or "discovery"
    metrics = []
    for k in range(1, 21):
        selected = [
            r
            for r in report.summary
            if r["task"] == task
            and r["window_days"] == evaluation.primary_window_days
            and r["k"] == k
            and r["day_weight"] in (None, evaluation.primary_day_weight)
        ]
        row = {
            name: None
            for name in (
                "ndcg",
                "binary_ndcg",
                "graded_ndcg",
                "recall",
                "repeat_recall",
                "ild",
                "novelty",
                "catalog_coverage",
            )
        }
        row.update(
            k=k,
            n_evaluated=0,
            n_evaluated_users=0,
            n_excluded=len({r.user_id for r in data.requests}),
            task=task,
            window_days=evaluation.primary_window_days,
            day_weight=evaluation.primary_day_weight,
        )
        for value in selected:
            row[value["metric"]] = value["value"]
            row.update(
                n_evaluated=value["n_evaluated"],
                n_evaluated_users=value["n_evaluated"],
                n_excluded=value["n_excluded"],
            )
        beyond_task = "repeat" if task == "repeat" else "new"
        beyond = report.beyond_accuracy or {}
        for value in beyond.get("metrics", ()):
            if value["task"] == beyond_task and value["k"] == k:
                row.update({name: value[name] for name in ("ild", "novelty", "catalog_coverage")})
        # ndcgは旧表示への別名。v2の主指標名には使用させない。
        row["ndcg"] = row["binary_ndcg"]
        metrics.append(row)
    requests = []
    for run in report.runs:
        request = run["request"]
        for row in run["metrics"]:
            requests.append(
                {
                    **row,
                    "request_id": request["request_id"],
                    "user_id": request["user_id"],
                    "history_count": len(request["history"]),
                }
            )
    metadata = report.metadata
    return {
        "protocol": "listening-v2",
        "metrics": metrics,
        "all_metrics": list(report.summary),
        "beyond_accuracy": report.beyond_accuracy,
        "requests": requests,
        "primary": {
            "task": task,
            "window_days": evaluation.primary_window_days,
            "day_weight": evaluation.primary_day_weight,
        },
        "efficiency": {
            "latency_p50": metadata["latency_p50"],
            "latency_p95": metadata["latency_p95"],
            "training_seconds": metadata["training_seconds"],
            **metadata["usage"]["metrics"],
        },
        "definitions": {
            "protocol": "listening-v2",
            "beyond_accuracy": (report.beyond_accuracy or {}).get(
                "definitions", "未測定: 旧v2成果物"
            ),
            "usage": metadata["usage"],
        },
    }
