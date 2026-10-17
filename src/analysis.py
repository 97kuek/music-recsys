"""Notebookの分析表。モデル学習・評価は共通Runnerを利用する。"""

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from src.data import BenchmarkDataset
from src.evaluation.benchmark import BenchmarkReport
from src.ingestion.types import Catalog

ReportInput = BenchmarkReport | Mapping[str, Any] | Path


def compare_reports(reports: Mapping[str, ReportInput]) -> list[dict[str, Any]]:
    """共通Runnerの結果を同じデータ・明示分割・Kで比較する。

    dataset_sha256は履歴・要求・正解を含む入力JSON全体のハッシュ。
    ローカルresults.jsonとMLflowの集計report.jsonを読み込める。
    """
    conditions = None
    rows = []
    for label, source in reports.items():
        if isinstance(source, Path):
            raw = json.loads(source.read_text(encoding="utf-8"))
        elif isinstance(source, BenchmarkReport):
            raw = asdict(source)
        else:
            raw = source
        metadata, summary = raw["metadata"], raw["summary"]
        current = {key: metadata.get(key) for key in ("dataset_id", "dataset_sha256", "top_k")}
        for key, value in current.items():
            if value is None or value == "":
                raise ValueError(f"report is missing {key}")
            if conditions is not None and value != conditions[key]:
                raise ValueError(f"reports have different {key}; comparison is not valid")
        conditions = current
        rows.append(
            {
                "label": label,
                "model": metadata["model_name"],
                **current,
                "seed": metadata["seed"],
                "training_seconds": metadata["training_seconds"],
                **summary,
            }
        )
    return rows


def request_rows(report: BenchmarkReport) -> list[dict[str, Any]]:
    """要求を連番で表示し、ユーザーID・履歴本体を分析表に含めない。"""
    rows = []
    for index, run in enumerate(report.runs, 1):
        evaluation = asdict(run.evaluation)
        evaluation.pop("request_id")
        rows.append(
            {
                "request_index": index,
                "history_count": len(run.request.history),
                "candidate_count": len(run.request.candidate_ids),
                "history_overlap_count": len(set(run.response.item_ids) & set(run.request.history)),
                "status": run.response.status,
                "recommendation_seconds": run.recommendation_seconds,
                **evaluation,
            }
        )
    return rows


def recommendation_rows(dataset: BenchmarkDataset, report: BenchmarkReport) -> list[dict[str, Any]]:
    """推薦曲の学習頻度と正解該当を、推薦器に正解を渡さず分析する。"""
    for key, expected in (
        ("dataset_id", dataset.dataset_id),
        ("dataset_sha256", dataset.sha256),
    ):
        if getattr(report.metadata, key) != expected:
            raise ValueError(f"report has different {key}")
    if any(request.top_k != report.metadata.top_k for request in dataset.requests):
        raise ValueError("report has different top_k")
    if tuple(run.request for run in report.runs) != dataset.requests:
        raise ValueError("report has different requests")
    popularity = Counter(row.item_id for row in dataset.train_interactions)
    truths = {truth.request_id: set(truth.relevant_item_ids) for truth in dataset.ground_truth}
    return [
        {
            "request_index": index,
            "rank": rank,
            "item_id": item_id,
            "train_events": popularity[item_id],
            "is_relevant": (
                item_id in truths[run.request.request_id]
                if run.evaluation.relevant_count > 0
                else None
            ),
        }
        for index, run in enumerate(report.runs, 1)
        for rank, item_id in enumerate(run.response.item_ids, 1)
    ]


def tag_rows(catalog: Catalog) -> list[dict[str, Any]]:
    """snapshotの曲タグを集計する。空・欠損タグを推薦結果として補完しない。"""
    weights: dict[str, list[int]] = {}
    for track in catalog.tracks:
        for tag, weight in (track.metadata or {}).get("tags", {}).items():
            weights.setdefault(tag, []).append(weight)
    return [
        {"tag": tag, "n_tracks": len(values), "mean_weight": fmean(values)}
        for tag, values in sorted(weights.items(), key=lambda row: (-len(row[1]), row[0]))
    ]


def popularity_rows(
    dataset: BenchmarkDataset, report: BenchmarkReport | None = None
) -> list[dict[str, Any]]:
    """カタログの全曲を学習頻度・推薦出現回数の分析表にする。"""
    popularity = Counter(row.item_id for row in dataset.train_interactions)
    exposure = (
        Counter(row["item_id"] for row in recommendation_rows(dataset, report))
        if report is not None
        else Counter()
    )
    return [
        {"item_id": item, "train_events": popularity[item], "recommended_count": exposure[item]}
        for item in dataset.catalog_ids
    ]


def dataset_request_rows(dataset: BenchmarkDataset) -> list[dict[str, int]]:
    """要求の構造を件数で確認し、生ユーザーID・履歴を返さない。"""
    return [
        {
            "request_index": index,
            "history_count": len(request.history),
            "candidate_count": len(request.candidate_ids),
            "top_k": request.top_k,
        }
        for index, request in enumerate(dataset.requests, 1)
    ]


def temporal_rows(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    """保存済み日別EDAの全件を累積する。表示範囲による除外はしない。"""
    daily = profile["daily_counts"]
    total = sum(row["n_events"] for row in daily)
    running = 0
    rows = []
    for row in daily:
        running += row["n_events"]
        rows.append(
            {
                "day": datetime.fromisoformat(row["day"]),
                "n_events": row["n_events"],
                "cumulative_fraction": running / total if total else None,
            }
        )
    return rows


def split_candidate_tables(profile: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """保存済み分割候補の集計を表示する。正式分割の作成・読込みはしない。

    cold理由は優先順cold_user→cold_item→seen→eligibleの排他的ペア数。
    分母も同じ評価期間のdistinct user-track pairsで、イベント数ではない。
    """
    events = [
        {
            "split": split,
            "n_events": profile["split_events"][split],
            "event_fraction": profile["split_fractions"][split],
        }
        for split in ("train", "validation", "test")
    ]
    eligibility = []
    for phase in ("validation", "test_train_only", "test_refit_train_validation"):
        values = profile[phase]
        reasons = values["pair_reasons"]
        total = sum(reasons.values())
        cold_user, cold_item = reasons.get("cold_user", 0), reasons.get("cold_item", 0)
        eligibility.append(
            {
                "condition": phase,
                "unique_pairs": total,
                "cold_user_pairs": cold_user,
                "cold_item_pairs": cold_item,
                "cold_user_fraction": cold_user / total if total else None,
                "cold_item_fraction": cold_item / total if total else None,
                "novel_truth_pairs": values["novel_pairs"],
                "users_with_novel_truth": values["novel_users"],
            }
        )
    return {"events": events, "eligibility": eligibility}
