"""Notebook共通の図・折りたたみ表・CSV表示。"""

import base64
from html import escape

import pandas as pd
from IPython.display import HTML, display

LABELS = {
    "ild": "多様性（ILD）",
    "novelty": "新規性",
    "catalog_coverage": "カタログ網羅率",
    "setting_id": "設定ID",
    "setting": "設定内容",
    "seed_std": "seed間標準偏差",
    "mean_seconds": "平均試行時間（秒）",
    "history_count": "履歴曲数",
    "candidate_count": "候補曲数",
    "recommended_rank": "推薦順位",
    "observation": "観測指標",
    "bin": "区間",
    "pairs": "ユーザー・曲ペア数",
    "window_days": "評価期間（日）",
    "latency_mean": "平均レイテンシ（秒）",
    "input_tokens": "入力トークン",
    "output_tokens": "出力トークン",
    "api_cost": "API料金（USD）",
    "llm_calls": "LLM呼出し数",
    "tool_calls": "tool呼出し数",
    "generation_calls": "生成要求数",
    "n_excluded": "除外数",
    "task": "タスク",
    "metric": "指標",
    "k": "K",
    "mean": "平均",
    "ci_low": "95% CI 下限",
    "ci_high": "95% CI 上限",
    "n_evaluated": "評価対象数",
    "experiment": "実験",
    "model": "モデル",
    "trial_id": "試行ID",
    "seed": "seed",
    "seconds": "試行時間（秒）",
    "item_id": "曲ID",
    "rank": "順位",
    "score": "スコア",
    "reason": "根拠",
    "in_history": "既聴",
    "user_train_events": "本人の再生数",
    "train_events": "全体の再生数",
    "user_id": "ユーザーID",
    "request_id": "要求ID",
    "users": "ユーザー数",
    "evaluable_users": "正解ありユーザー数",
    "mean_candidates": "平均候補数",
    "mean_positives": "平均正解数",
}


def table_html(frame, title, filename):
    """表示と同じ行・列順の全件CSVを埋め込む / 元データは変更しない。"""
    frame = pd.DataFrame(frame)
    csv = frame.to_csv(index=False).encode("utf-8-sig")
    encoded = base64.b64encode(csv).decode("ascii")
    table = frame.rename(columns=LABELS).to_html(
        index=False, escape=True, na_rep="未測定", float_format=repr
    )
    return (
        f"<details><summary>{escape(title)} · {len(frame)}行 × {len(frame.columns)}列</summary>"
        f'<p><a download="{escape(filename, quote=True)}.csv" '
        f'href="data:text/csv;base64,{encoded}">CSVを保存（全行・全精度）</a></p>'
        '<div style="max-height:320px;overflow:auto;border:1px solid #ddd">'
        f"{table}</div></details>"
    )


def show_table(frame, title, filename):
    display(HTML(table_html(frame, title, filename)))


def show_details(text, title="詳細"):
    display(
        HTML(
            f'<details><summary>{escape(title)}</summary><pre style="max-height:260px;overflow:auto">{escape(str(text))}</pre></details>'
        )
    )


def show_state(state):
    print("状態:", state["label"])
    for warning in state.get("warnings", []):
        print("注意:", warning.replace("。", " / ").rstrip(" / "))
    print("次:", state["next_action"])


def show_metric_group(frame, title, filename):
    """図を先に表示し、その直下に全Kの正確な値を置く。"""
    from src.interfaces.notebook.evaluation_figures import accuracy_figures, show_figures

    if frame.empty:
        print(f"{title}: 未指定または未測定")
        return
    show_figures(accuracy_figures(frame))
    columns = [
        "experiment",
        "task",
        "metric",
        "k",
        "mean",
        "ci_low",
        "ci_high",
        "n_evaluated",
        "n_excluded",
    ]
    columns = [c for c in columns if c in frame]
    show_table(
        frame[columns].sort_values(
            [c for c in ("experiment", "task", "metric", "k") if c in frame]
        ),
        title,
        filename,
    )


def show_factor_overview(config):
    """Display the selected factor model before the remaining evaluation aspects."""
    import pandas as pd

    from src.infrastructure.research.results import read_result
    from src.interfaces.notebook.evaluation_figures import factor_figures, show_figures

    report = read_result(config)
    print("選択モデル内の比較 / Binary NDCG → Recall → Repeat Recall → Latency → 学習時間")
    print("NDCGの縦軸は差を読み取るため拡大 / GPUレイテンシはバッチ処理時間の按分")
    show_figures(
        factor_figures(
            pd.DataFrame(report["metrics"]), config.model, config.evaluation.primary_window_days
        )
    )
