"""Notebookが共有する探索状態・保存結果の比較・描画。学習は実行しない。"""

import fcntl
import shlex
from collections import defaultdict
from statistics import fmean

from src.interfaces.cli.experiment_search import (
    _read_sealed,
    _verified_trial,
    context,
    load_final_test,
    load_locked_selection,
    load_selection,
    load_study,
    ranked_settings,
    read_json,
    trial_rows,
)
from src.interfaces.notebook.experiment_analysis import BootstrapConfig, cluster_statistics


def validation_overview(config):
    """01で探索と同じ入力・候補・正解の評価可能性を確認する。"""
    import pandas as pd

    if config.protocol == "listening-v2":
        from src.interfaces.notebook.full_analysis import overview

        return overview(config)
    data, conditions = context(config)
    truths = {t.request_id: set(t.relevant_item_ids) for t in data.ground_truth}
    rows = []
    for request in data.requests:
        eligible = set(request.candidate_ids).difference(request.history)
        rows.append(
            {
                "user_id": request.user_id,
                "request_id": request.request_id,
                "history_count": len(request.history),
                "candidate_count": len(eligible),
                "positive_count": len(truths[request.request_id] & eligible),
            }
        )
    frame = pd.DataFrame(rows)
    return (
        conditions,
        frame,
        {
            "phase": "validation",
            "requests": len(frame),
            "users": frame.user_id.nunique(),
            "evaluable_users": frame.loc[frame.positive_count > 0, "user_id"].nunique(),
            "empty_truth_requests": int((frame.positive_count == 0).sum()),
            "training_events": len(data.train_interactions),
            "catalog_items": len(data.catalog_ids),
        },
    )


def workflow_state(config):
    from src.interfaces.notebook.full_analysis import is_full, read_result

    if is_full(config):
        if not (config.output / "result.json").exists():
            return {
                "status": "running_or_interrupted",
                "label": "全件評価の結果は未確定",
                "next_action": "run.logと実行プロセスを確認",
                "warnings": [],
            }
        try:
            result = read_result(config)
        except ValueError as error:
            return {
                "status": "mismatch",
                "label": "条件不一致",
                "next_action": str(error),
                "warnings": [],
            }
        return {
            "status": "validation_complete",
            "label": "全件Validation完了",
            "next_action": "02で4観点を確認 / 最終Testは未実行",
            "warnings": [],
            "users": result["population"]["users"],
        }
    commands = (
        f"python -m src.interfaces.cli.experiment_search --config {shlex.quote(str(config.path))}"
    )
    result = {
        "status": "not_run",
        "label": "未実行",
        "next_action": f"01_dataで確認後、{commands} smoke",
        "warnings": [],
    }
    try:
        study = load_study(config)
        if not study:
            return result
        state = study["status"]
        result["search_status"] = state
        result.update(
            planned_trials=study.get("planned_trials", 0),
            successful_trials=study.get("successful_trials", 0),
            failed_trials=study.get("failed_trials", 0),
            elapsed_seconds=study.get("elapsed_seconds"),
            stage=study.get("history", [{}])[-1].get("stage") if study.get("history") else None,
        )
        if state == "running":
            with (config.output / ".lock").open("a") as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(stream, fcntl.LOCK_UN)
                    return {
                        **result,
                        "status": "interrupted",
                        "label": "中断・状態未確定",
                        "next_action": f"保存済み試行を確認し、{commands} search で再開",
                    }
                except BlockingIOError:
                    return {
                        **result,
                        "status": "running",
                        "label": "実行中",
                        "next_action": "CLIの終了を待ち、02_analysisを再読込み",
                    }
        if state in {"incomplete", "failed"}:
            return {
                **result,
                "status": state,
                "label": "探索未完了" if state == "incomplete" else "失敗",
                "next_action": f"エラー・モデル実装・予算を確認し、{commands} search。データ条件は01_dataへ",
            }
        if state == "smoke_passed":
            return {
                **result,
                "status": state,
                "label": "Smoke成功",
                "next_action": f"範囲と予算を確認し、{commands} search",
            }
        rows = trial_rows(config)
        if (
            rows
            and max((r.get("n_evaluated_users", r.get("n_evaluated", 0)) for r in rows), default=0)
            < 10
        ):
            result["warnings"].append(
                "評価可能ユーザーが10人未満。01_dataで条件を確認し、小規模結果を一般化しないでください"
            )
        if config.metric is None:
            return {
                **result,
                "status": "selection_pending",
                "label": "主指標の指定待ち",
                "next_action": "主指標を開始前に指定した別の実験を用意。自動選択はしません",
            }
        ranked = ranked_settings(config)
        if len(ranked) == 1:
            result["warnings"].append("比較設定は1件。探索上の最適性を示す結果ではありません")
        else:
            if abs(ranked[0]["score"] - ranked[1]["score"]) <= 0.001:
                result["warnings"].append(
                    "最良と次点の差が0.001以下。複雑さ・時間・seed差も比較してください"
                )
        for key, values in config.grid.items():
            if (
                len(values) > 1
                and all(type(v) in (int, float) for v in values)
                and ranked[0]["parameters"][key] in (min(values), max(values))
            ):
                result["warnings"].append(
                    f"最良設定の{key}が探索範囲の端。02で範囲拡張を検討してください"
                )
        if not (config.output / "selection.json").exists():
            return {
                **result,
                "status": "selection_pending",
                "label": "最適設定の確定待ち",
                "next_action": f"02_analysisで最良・次点を比較し、{commands} select",
            }
        selected = load_selection(config)
        if (config.output / "locked-selection.json").exists():
            locked = load_locked_selection(config)
            if locked["selection_sha256"] != selected["content_sha256"]:
                raise ValueError("固定条件と選択が不一致")
            if (config.output / "final-test.json").exists():
                final = load_final_test(config)
                if final["locked_sha256"] != locked["content_sha256"]:
                    raise ValueError("Testと固定条件が不一致")
                return {
                    **result,
                    "status": "test_" + final["status"],
                    "label": "Test成功" if final["status"] == "succeeded" else "Test未完了",
                    "next_action": (
                        "02_analysisのFinal Comparisonで同条件モデルを比較。Testから探索へ戻さないでください"
                        if final["status"] == "succeeded"
                        else f"実行プロセスとエラーを確認し、停止済みなら同じ固定条件で {commands} test。成功seedは再利用します"
                    ),
                }
            return {
                **result,
                "status": "test_ready",
                "label": "Test実行可能",
                "next_action": f"固定条件でTrain+Validationを再学習：{commands} test",
            }
        review_path = config.output / "review.json"
        if review_path.exists():
            review = _read_sealed(review_path)
            if review["selection_sha256"] == selected["content_sha256"] and not review["accepted"]:
                return {
                    **result,
                    "status": "review_rejected",
                    "label": "推薦結果の改善待ち",
                    "next_action": "02_analysisのValidation探索かモデル実装へ戻ってください。Testへは進めません",
                }
            if review["selection_sha256"] == selected["content_sha256"] and review["accepted"]:
                return {
                    **result,
                    "status": "freeze_pending",
                    "label": "最終設定の固定待ち",
                    "next_action": f"Test入力を確認し、{commands} freeze",
                }
        return {
            **result,
            "status": "review_pending",
            "label": "03での確認待ち",
            "next_action": "03_recommendation.ipynbで選択設定のValidation推薦を観察。問題があれば02_analysisへ戻る",
        }
    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
        return {
            **result,
            "status": "mismatch",
            "label": "比較条件不一致・判定不能",
            "next_action": f"01_dataとTOML・保存物を確認してください：{error}",
        }


def search_table(config):
    import pandas as pd

    rows = trial_rows(config)
    return pd.DataFrame(
        [{**r, **{f"param:{k}": v for k, v in r["parameters"].items()}} for r in rows]
    )


def _evaluation_label(config):
    if config.protocol != "listening-v2":
        return ""
    return (
        f"listening-v2 | {config.task or 'discovery (diagnostic)'} | "
        f"{config.evaluation.primary_window_days} days | "
        f"day weight {config.evaluation.primary_day_weight:g}"
    )


def plot_search(config):
    import matplotlib.pyplot as plt

    frame = search_table(config)
    names = sorted(config.grid)
    metric = config.metric or "ndcg"
    if (
        frame.empty
        or not names
        or len(names) > 2
        or metric not in frame
        or not frame[metric].notna().any()
    ):
        return None
    valid = frame.dropna(subset=[metric])
    fig, ax = plt.subplots(figsize=(7, 4))
    title = f"Validation · {config.metric or 'diagnostic'}@{config.primary_k}"
    if len(names) == 1:
        key = f"param:{names[0]}"
        stats = valid.groupby(key, dropna=False)[metric].agg(["mean", "std"]).reset_index()
        ax.plot([str(x) for x in stats[key]], stats["mean"], "o-", color="#267d85")
        ax.set_xlabel(names[0])
        ax.set_ylabel(f"{metric}@{config.primary_k}")
    else:
        x, y = [f"param:{n}" for n in names]
        pivot = valid.pivot_table(index=y, columns=x, values=metric, aggfunc="mean")
        im = ax.imshow(pivot.to_numpy(), aspect="auto", cmap="viridis")
        ax.set_xticks(range(len(pivot.columns)), [str(v) for v in pivot.columns])
        ax.set_yticks(range(len(pivot.index)), [str(v) for v in pivot.index])
        ax.set_xlabel(names[0])
        ax.set_ylabel(names[1])
        for j in range(len(pivot.index)):
            for i in range(len(pivot.columns)):
                value = pivot.iloc[j, i]
                ax.text(
                    i,
                    j,
                    f"{value:.3f}" if value == value else "unmeasured",
                    ha="center",
                    color="white",
                )
        fig.colorbar(im, ax=ax, label=f"{metric}@{config.primary_k}")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    return fig


def parameter_sensitivity(config):
    """最良設定（未選択指標なら最初の成功設定）を基準に他の変数を固定する。"""
    import pandas as pd

    rows = trial_rows(config)
    valid = [row for row in rows if row["status"] == "succeeded"]
    if not valid:
        return pd.DataFrame()
    study = load_study(config)
    anchor = (
        ranked_settings(config)[0]["parameters"]
        if config.metric and study["status"] == "succeeded"
        else valid[0]["parameters"]
    )
    metric, result = config.metric or "ndcg", []
    for name in sorted(config.grid):
        fixed = {key: value for key, value in anchor.items() if key != name}
        for value in config.grid[name]:
            matches = [
                row
                for row in valid
                if row["parameters"].get(name) == value
                and all(row["parameters"].get(key) == x for key, x in fixed.items())
            ]
            measured = [row[metric] for row in matches if row.get(metric) is not None]
            result.append(
                {
                    "varied_parameter": name,
                    "value": value,
                    "fixed_parameters": fixed,
                    "mean": fmean(measured) if len(measured) == len(config.seeds) else None,
                    "n_seeds": len(measured),
                }
            )
    frame = pd.DataFrame(result)
    frame.attrs["evaluation_label"] = _evaluation_label(config)
    return frame


def plot_sensitivity(frame, *, metric, k):
    import matplotlib.pyplot as plt

    if frame.empty:
        return None
    # 変数が多い場合も画面を埋めない。全条件と値は併記する表で確認できる。
    names = list(frame.varied_parameter.unique())[:3]
    fig, axes = plt.subplots(1, len(names), figsize=(4 * len(names), 3.5), squeeze=False)
    for name, ax in zip(names, axes.flat, strict=True):
        rows = frame[frame.varied_parameter == name]
        ax.plot([str(v) for v in rows.value], rows["mean"].astype(float), "o-", color="#267d85")
        ax.set(
            title=f"Validation | {metric}@{k} | seed mean\nOther parameters fixed (see table)"
            + (
                f"\n{frame.attrs['evaluation_label']}"
                if frame.attrs.get("evaluation_label")
                else ""
            ),
            xlabel=name,
            ylabel=f"{metric}@{k}",
        )
    fig.tight_layout()
    return fig


def _trial_bundles(config, trial_ids):
    study = load_study(config)
    bundles = []
    for key in trial_ids:
        if not study or key not in study["planned"] or key not in study["trials"]:
            raise ValueError("診断trialが見つかりません")
        trial = study["trials"][key]
        if not _verified_trial(config, trial):
            raise ValueError("成功済みtrialだけを分析できます")
        bundles.append(read_json(config.output / trial["curves"]))
    return bundles


def trial_curves(config, trial_id, *, resamples=2000):
    """指定した1試行の診断用Reader。設定選択やTestの許可を変更しない。"""
    frame = _curve_frame(_trial_bundles(config, [trial_id]), config.model, "validation", resamples)
    frame.attrs["diagnostic_trial_id"] = trial_id
    return frame


def selected_curves(config, *, resamples=2000):
    selected = load_selection(config)
    return _curve_frame(
        _trial_bundles(config, selected["trial_ids"]), config.model, "validation", resamples
    )


def _curve_frame(bundles, model, phase, resamples):
    import pandas as pd

    if any(b.get("protocol") == "listening-v2" for b in bundles):
        if not all(b.get("protocol") == "listening-v2" for b in bundles):
            raise ValueError("比較条件不一致: protocols")
        from src.interfaces.notebook.listening_analysis import curve_frame

        return curve_frame(bundles, model, phase, resamples)

    request_values = defaultdict(list)
    for curves in bundles:
        for r in curves["requests"]:
            for metric in ("ndcg", "recall"):
                if r[metric] is not None:
                    request_values[(r["k"], metric, r["user_id"], r["request_id"])].append(
                        r[metric]
                    )
    rows = []
    for k in range(1, 21):
        means = [b["metrics"][k - 1] for b in bundles]
        for metric in ("ndcg", "recall"):
            values = [
                (user, fmean(v))
                for (kk, m, user, _), v in request_values.items()
                if kk == k and m == metric
            ]
            stats = cluster_statistics(values, BootstrapConfig(resamples=resamples, seed=42))
            row = {"phase": phase, "model": model, "metric": metric, "k": k, **stats}
            for name in ("graded_ndcg", "ild", "novelty", "catalog_coverage"):
                xs = [r[name] for r in means if r[name] is not None]
                row[name] = fmean(xs) if xs else None
            for name in bundles[0]["efficiency"]:
                xs = [b["efficiency"][name] for b in bundles if b["efficiency"][name] is not None]
                row[name] = fmean(xs) if xs else None
            rows.append(row)
    return pd.DataFrame(rows)


def history_performance(config):
    import pandas as pd

    selected, study = load_selection(config), load_study(config)
    if config.protocol == "listening-v2":
        from src.interfaces.notebook.listening_analysis import history_rows

        return history_rows(_trial_bundles(config, selected["trial_ids"]), config)
    grouped = defaultdict(list)
    for key in selected["trial_ids"]:
        for r in read_json(config.output / study["trials"][key]["curves"])["requests"]:
            if r["k"] == config.primary_k and r["ndcg"] is not None:
                grouped[(r["user_id"], r["request_id"], r["history_count"])].append(r["ndcg"])
    rows = defaultdict(list)
    for (_, _, n), values in grouped.items():
        label = "0–4" if n < 5 else "5–19" if n < 20 else "20–99" if n < 100 else "100+"
        rows[label].append(fmean(values))
    return pd.DataFrame(
        [
            {
                "phase": "validation",
                "history_count": label,
                "ndcg": fmean(values),
                "n_requests": len(values),
            }
            for label, values in rows.items()
        ]
    )


def final_comparison(configs):
    """同じTest入力・評価条件だけを比較する。未実行は空、混在はエラー。"""
    import pandas as pd

    rows, conditions = [], None
    for config in configs:
        path = config.output / "final-test.json"
        if not path.exists():
            continue
        final = load_final_test(config)
        if final["status"] != "succeeded":
            raise ValueError("Testが未完了のモデルは最終比較できません")
        locked = _read_sealed(config.output / "locked-selection.json")
        if final["locked_sha256"] != locked["content_sha256"]:
            raise ValueError("比較条件不一致: Testの固定設定")
        if conditions is not None and final["conditions"] != conditions:
            raise ValueError("比較条件不一致: Testは同一入力・評価方法で比較してください")
        conditions = final["conditions"]
        bundles = []
        for trial in final["trials"].values():
            _verified_trial(config, trial)
            bundles.append(read_json(config.output / trial["curves"]))
        rows.append(
            _curve_frame(bundles, config.model, "test", 2000).assign(experiment=config.name)
        )
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def plot_performance(frame, *, phase="Validation"):
    import matplotlib.pyplot as plt

    if "task" in frame:
        from src.interfaces.notebook.listening_analysis import plot_curves

        return plot_curves(frame, phase=phase)

    if frame.empty:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.5))
    for ax, metric in zip(axes, ("ndcg", "recall"), strict=True):
        rows = frame[frame.metric == metric].sort_values("k")
        ax.plot(rows.k, rows["mean"].astype(float), "-", color="#267d85")
        measured = rows.dropna(subset=["ci_low", "ci_high"])
        if not measured.empty:
            ax.fill_between(
                measured.k,
                measured.ci_low.astype(float),
                measured.ci_high.astype(float),
                color="#267d85",
                alpha=0.15,
            )
        ax.set(
            title=f"{phase} | Binary {metric.upper()} | seed mean; user 95% CI",
            xlabel="K (same top-20 ranking)",
            ylabel=metric.upper(),
            ylim=(-0.02, 1.02),
        )
    fig.tight_layout()
    return fig


def plot_final(frame):
    import matplotlib.pyplot as plt

    if "task" in frame:
        from src.interfaces.notebook.listening_analysis import plot_curves

        return plot_curves(frame, phase="Test")

    if frame.empty:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.5))
    for ax, metric in zip(axes, ("ndcg", "recall"), strict=True):
        for label, group in frame[frame.metric == metric].groupby("experiment"):
            group = group.sort_values("k")
            ax.plot(group.k, group["mean"].astype(float), label=label)
            valid = group.dropna(subset=["ci_low", "ci_high"])
            if not valid.empty:
                ax.fill_between(
                    valid.k, valid.ci_low.astype(float), valid.ci_high.astype(float), alpha=0.1
                )
        ax.set(
            title=f"Test | Binary {metric.upper()} | seed mean; user 95% CI",
            xlabel="K",
            ylabel=metric.upper(),
            ylim=(-0.02, 1.02),
        )
        ax.legend()
    fig.tight_layout()
    return fig


def ground_truth_rows(profile):
    import pandas as pd

    rows = []
    for label, window in profile.get("windows", {}).items():
        for category, data in window["categories"].items():
            rows.append(
                {
                    "scope": "full-event reference",
                    "window": label,
                    "window_days": window["days"],
                    "category": category,
                    "users_with_truth": data["users_with_positive_pairs"],
                    "pairs": data["pairs"],
                    "play_count_median": data["play_count_quantiles"]["0.5"],
                    "active_days_median": data["active_days_quantiles"]["0.5"],
                    "multiday_fraction": data["threshold_counts"]["active_days"]["2"]
                    / data["pairs"]
                    if data["pairs"]
                    else None,
                }
            )
    return pd.DataFrame(rows)


def tradeoff_points(config):
    """全設定の同じK=10を集計する。欠損は測定済みseedだけの平均で埋めない。"""
    import pandas as pd

    study = load_study(config)
    accuracy_metric = config.metric or (
        "binary_ndcg" if config.protocol == "listening-v2" else "ndcg"
    )
    grouped = defaultdict(list)
    if not study:
        return pd.DataFrame()
    for key in study["planned"]:
        trial = study["trials"][key]
        if _verified_trial(config, trial):
            metrics = read_json(config.output / trial["curves"])["metrics"]
            row = next(r for r in metrics if r["k"] == 10)
            label = str(trial["parameters"]) if trial["parameters"] else config.model
            grouped[label].append(row)
    rows = []
    for index, (label, values) in enumerate(grouped.items(), 1):
        rows.append(
            {
                "setting_id": f"S{index}",
                "setting": label,
                **{
                    metric: fmean(r[metric] for r in values)
                    if len(values) == len(config.seeds)
                    and all(r[metric] is not None for r in values)
                    else None
                    for metric in tuple(
                        dict.fromkeys(
                            ("ndcg", accuracy_metric, "ild", "novelty", "catalog_coverage")
                        )
                    )
                },
            }
        )
    frame = pd.DataFrame(rows)
    frame.attrs["model"] = config.model
    metric = config.metric or ("binary_ndcg" if config.protocol == "listening-v2" else "ndcg")
    if not frame.empty:
        frame["accuracy"] = frame[metric]
    frame.attrs["accuracy_metric"] = metric
    frame.attrs["evaluation_label"] = _evaluation_label(config)
    return frame


def plot_tradeoffs(frame, *, phase="Validation"):
    import matplotlib.pyplot as plt

    if frame.empty:
        return None
    rows = frame
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
    for ax, metric in zip(axes, ("ild", "novelty", "catalog_coverage"), strict=True):
        accuracy = "accuracy" if "accuracy" in rows else "ndcg"
        accuracy_label = frame.attrs.get("accuracy_metric", "ndcg")
        values = rows.dropna(
            subset=[metric] if metric == "catalog_coverage" else [metric, accuracy]
        )
        if values.empty:
            ax.text(0.5, 0.5, "Unmeasured", ha="center", transform=ax.transAxes)
        else:
            if metric == "catalog_coverage":
                ax.bar(range(len(values)), values[metric], color="#267d85")
                ax.set_xticks(range(len(values)), values.setting_id)
            else:
                ax.scatter(values[metric], values[accuracy], color="#267d85")
                for (x, y), points in values.groupby([metric, accuracy]):
                    ax.annotate(
                        ", ".join(points.setting_id),
                        (x, y),
                        fontsize=8,
                        xytext=(3, 3),
                        textcoords="offset points",
                    )
                ax.margins(x=0.2, y=0.2)
        if metric == "catalog_coverage":
            ax.set(
                title="Coverage@10",
                xlabel="Parameter setting",
                ylabel="Catalog coverage",
            )
        else:
            ax.set(
                title=f"{metric}@10",
                xlabel=metric,
                ylabel=f"{accuracy_label}@10",
            )
        ax.title.set_fontsize(10)
    fig.tight_layout()
    return fig
