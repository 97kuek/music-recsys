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


def metric_groups(frame, config):
    """主条件の曲線を主指標と補助指標に分ける / 未指定時は全て診断。"""
    from music_recsys.listening_analysis import primary_rows

    rows = primary_rows(frame)
    primary = (rows.task == config.task) & (rows.metric == config.metric)
    return rows[primary].copy(), rows[~primary].copy()


def show_metric_group(frame, title, filename):
    """図を先に表示し、その直下に全Kの正確な値を置く。"""
    import matplotlib.pyplot as plt

    if frame.empty:
        print(f"{title}: 未指定または未測定")
        return
    groups = list(frame.groupby(["task", "metric"], sort=False))
    cols = min(2, len(groups))
    row_count = (len(groups) + cols - 1) // cols
    fig, axes = plt.subplots(row_count, cols, figsize=(6 * cols, 3.5 * row_count), squeeze=False)
    for ax in list(axes.flat)[len(groups) :]:
        ax.set_visible(False)
    for ax, ((task, metric), rows) in zip(axes.flat, groups, strict=False):
        curves = rows.groupby("experiment", sort=False) if "experiment" in rows else [(None, rows)]
        for label, values in curves:
            values = values.sort_values("k")
            line = ax.plot(values.k, values["mean"], label=label)[0]
            ci = values.dropna(subset=["ci_low", "ci_high"])
            ax.fill_between(
                ci.k.to_numpy(float),
                ci.ci_low.to_numpy(float),
                ci.ci_high.to_numpy(float),
                color=line.get_color(),
                alpha=0.12,
            )
        task_label = {"discovery": "New", "repeat": "Repeat", "retention_proxy": "New (proxy)"}[
            task
        ]
        metric_label = {
            "binary_ndcg": "Binary NDCG",
            "graded_ndcg": "Graded NDCG",
            "repeat_recall": "Recall",
            "recall": "Recall",
        }[metric]
        ax.set(
            title=f"{task_label} · {metric_label}", xlabel="K", ylabel="Score", ylim=(-0.02, 1.02)
        )
        if "experiment" in rows:
            ax.legend(fontsize=8)
    fig.tight_layout()
    plt.show()
    columns = ["experiment", "task", "metric", "k", "mean", "ci_low", "ci_high", "n_evaluated"]
    columns = [c for c in columns if c in frame]
    show_table(
        frame[columns].sort_values(
            [c for c in ("experiment", "task", "metric", "k") if c in frame]
        ),
        title,
        filename,
    )
