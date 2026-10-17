"""全モデル共通の4観点 / 選択用の主指標によって表示を省略しない。"""

from src.interfaces.notebook.listening_analysis import primary_rows
from src.interfaces.notebook.notebook_display import show_metric_group, show_table

ACCURACY = {
    "新規発見": (("discovery", "binary_ndcg"), ("discovery", "recall")),
    "継続的な嗜好": (
        ("retention_proxy", "graded_ndcg"),
        ("repeat", "graded_ndcg"),
        ("repeat", "repeat_recall"),
    ),
}
DIVERSITY = ("ild", "catalog_coverage", "novelty")
EFFICIENCY = (
    "latency_mean",
    "input_tokens",
    "output_tokens",
    "api_cost",
    "llm_calls",
    "tool_calls",
)


def accuracy_groups(frame):
    rows = primary_rows(frame)
    rows = rows[rows.k.between(2, 20)]
    return {
        name: rows[[pair in pairs for pair in zip(rows.task, rows.metric, strict=True)]].copy()
        for name, pairs in ACCURACY.items()
    }


def diversity_table(frame):
    rows = primary_rows(frame)
    rows = rows[rows.k.between(2, 20) & rows.task.isin(["discovery", "repeat"])].copy()
    rows["task"] = rows.task.replace({"discovery": "new"})
    columns = [
        "experiment",
        "model",
        "task",
        "k",
        *DIVERSITY,
        "ild_evaluated",
        "ild_excluded",
        "novelty_evaluated",
        "novelty_excluded",
        "candidate_items",
        "exposed_items",
    ]
    return rows[[c for c in columns if c in rows]].drop_duplicates().reset_index(drop=True)


def efficiency_table(frame):
    columns = [
        "experiment",
        "model",
        *EFFICIENCY,
        "generation_calls",
        "training_seconds",
        "latency_p50",
        "latency_p95",
    ]
    # 未測定の列も残す / 欠測をゼロにしない
    result = frame.reindex(columns=[c for c in columns if c in frame or c in EFFICIENCY])
    return result.drop_duplicates().reset_index(drop=True)


def show_accuracy(frame, prefix="validation"):
    for name, rows in accuracy_groups(frame).items():
        print(name, "/ K=2〜20 / 帯はユーザー単位95% CI")
        show_metric_group(
            rows, name, f"{prefix}-{'discovery' if name == '新規発見' else 'preference'}"
        )


def show_other_aspects(frame, prefix="validation"):
    from src.interfaces.notebook.evaluation_figures import (
        diversity_figures,
        efficiency_figures,
        show_figures,
    )

    beyond = diversity_table(frame)
    print("多様性・発見性 / 正解なしを含む全要求 / 窓・日数重みで重複集計しない")
    show_figures(diversity_figures(beyond))
    show_table(beyond, "多様性・発見性", f"{prefix}-diversity")
    efficiency = efficiency_table(frame)
    print("効率性 / 先頭20曲を1回生成 / Kごとの再生成なし / 時間のCSV単位は秒")
    show_figures(efficiency_figures(efficiency))
    show_table(efficiency, "効率性", f"{prefix}-efficiency")
    print("未測定は欠測値 / トークン・料金・呼出し回数をゼロで補完しない")
    return beyond, efficiency
