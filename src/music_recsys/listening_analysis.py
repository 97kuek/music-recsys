"""保存済みv2指標・観測の表示専用処理。推薦・採点・設定選択を実行しない。"""

from collections import defaultdict
from statistics import fmean

from music_recsys.candidates import eligible_candidates
from music_recsys.experiment_analysis import BootstrapConfig, cluster_statistics


def curve_frame(bundles, model, phase, resamples):
    import pandas as pd

    fields = ("task", "window_days", "k", "day_weight", "metric")
    values = defaultdict(lambda: defaultdict(list))
    conditions = {}
    for bundle in bundles:
        for row in bundle["all_metrics"]:
            key = tuple(row[f] for f in fields)
            conditions[key] = row["n_requests"]
        for row in bundle["requests"]:
            if row["value"] is not None:
                key = tuple(row[f] for f in fields)
                values[key][(row["user_id"], row["request_id"])].append(row["value"])
    rows = []
    for key, n_requests in conditions.items():
        observations = []
        for (user, _), xs in values[key].items():
            if len(xs) != len(bundles):
                raise ValueError("比較条件不一致: v2 user / seed population")
            observations.append((user, fmean(xs)))
        primary = bundles[0]["primary"]
        compute_ci = key[1] == primary["window_days"] and (
            key[4] != "graded_ndcg" or key[3] == primary["day_weight"]
        )
        row = {
            "phase": phase,
            "protocol": "listening-v2",
            "model": model,
            **dict(zip(fields, key, strict=True)),
            **cluster_statistics(
                observations, BootstrapConfig(resamples=resamples, seed=42), compute_ci=compute_ci
            ),
        }
        row.update(
            n_requests=n_requests,
            n_excluded=n_requests - row["n_evaluated"],
            ild=None,
            novelty=None,
            catalog_coverage=None,
        )
        for name in (
            "ild",
            "novelty",
            "catalog_coverage",
            "ild_evaluated",
            "ild_excluded",
            "novelty_evaluated",
            "novelty_excluded",
            "candidate_items",
            "exposed_items",
        ):
            task = "repeat" if key[0] == "repeat" else "new"
            xs = [
                next(
                    (
                        r.get(name)
                        for r in (b.get("beyond_accuracy") or {}).get("metrics", ())
                        if r["task"] == task and r["k"] == key[2]
                    ),
                    None,
                )
                for b in bundles
            ]
            row[name] = fmean(xs) if all(x is not None for x in xs) else None
        for name in bundles[0]["efficiency"]:
            xs = [b["efficiency"][name] for b in bundles]
            row[name] = fmean(xs) if all(x is not None for x in xs) else None
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame.attrs["primary"] = bundles[0]["primary"]
    return frame


def primary_rows(frame):
    primary = frame.attrs.get("primary", {})
    if frame.empty or not primary:
        return frame
    return frame[
        (frame.window_days == primary["window_days"])
        & ((frame.metric != "graded_ndcg") | (frame.day_weight == primary["day_weight"]))
    ]


def plot_curves(frame, *, phase):
    import matplotlib.pyplot as plt

    frame = primary_rows(frame)
    if frame.empty:
        return None
    if "experiment" in frame and frame.experiment.nunique() > 2:
        return _plot_model_comparison(frame, phase=phase)
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
    primary = frame.attrs.get("primary", {})
    for ax, tasks, title in (
        (axes[0], {"discovery", "retention_proxy"}, "New items: Binary / Graded NDCG, Recall"),
        (axes[1], {"repeat"}, "Repeat items: Graded NDCG, Repeat Recall"),
    ):
        selected = frame[frame.task.isin(tasks)]
        group_fields = ["task", "metric"]
        if "experiment" in selected:
            group_fields.insert(0, "experiment")
        drawn = False
        for key, rows in selected.groupby(group_fields, dropna=False):
            rows = rows.sort_values("k")
            valid = rows.dropna(subset=["mean"])
            if valid.empty:
                continue
            drawn = True
            label = " | ".join(str(x) for x in key)
            line = ax.plot(valid.k, valid["mean"].astype(float), label=label)[0]
            ci = valid.dropna(subset=["ci_low", "ci_high"])
            if not ci.empty:
                ax.fill_between(
                    ci.k,
                    ci.ci_low.astype(float),
                    ci.ci_high.astype(float),
                    color=line.get_color(),
                    alpha=0.1,
                )
        if not drawn:
            ax.text(0.5, 0.5, "No evaluable users", ha="center", transform=ax.transAxes)
        else:
            ax.legend(fontsize=7)
        ax.set(
            title=f"{phase} | {title}",
            xlabel="K (same top-20 ranking)",
            ylabel="NDCG / Recall",
            ylim=(-0.02, 1.02),
        )
        ax.title.set_fontsize(10)
    fig.suptitle(
        f"listening-v2 | {primary.get('window_days', '?')} days | day weight {primary.get('day_weight', '?')} | seed mean; user 95% CI",
        fontsize=10,
    )
    fig.tight_layout()
    return fig


def _plot_model_comparison(frame, *, phase):
    """多数モデルは指標ごとに分け、共通凡例を曲線の外へ置く。"""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    labels = sorted(frame.experiment.unique())
    palette = plt.get_cmap("tab10")
    colors = {label: palette(i % 10) for i, label in enumerate(labels)}
    panels = (
        ("discovery", "binary_ndcg", "New | Binary NDCG"),
        ("discovery", "recall", "New | Recall"),
        ("retention_proxy", "graded_ndcg", "New | Graded NDCG (provisional)"),
        ("repeat", "graded_ndcg", "Repeat | Graded NDCG (provisional)"),
        ("repeat", "repeat_recall", "Repeat | Recall"),
    )
    for ax, (task, metric, title) in zip(axes.flat, panels, strict=False):
        selected = frame[(frame.task == task) & (frame.metric == metric)]
        for label, rows in selected.groupby("experiment"):
            rows = rows.dropna(subset=["mean"]).sort_values("k")
            if rows.empty:
                continue
            ax.plot(rows.k, rows["mean"].astype(float), color=colors[label], label=label)
            ci = rows.dropna(subset=["ci_low", "ci_high"])
            if not ci.empty:
                ax.fill_between(
                    ci.k,
                    ci.ci_low.astype(float),
                    ci.ci_high.astype(float),
                    color=colors[label],
                    alpha=0.08,
                )
        if not ax.lines:
            ax.text(0.5, 0.5, "No evaluable users", ha="center", transform=ax.transAxes)
        ax.set(
            title=f"{phase} | {title}",
            xlabel="K (same top-20 ranking)",
            ylabel=metric,
            ylim=(-0.02, 1.02),
        )
        ax.title.set_fontsize(10)
    legend = axes.flat[-1]
    legend.set_axis_off()
    legend.legend(
        [Line2D([0], [0], color=colors[label]) for label in labels],
        labels,
        title="Models / experiments",
        loc="center",
        fontsize=9,
        frameon=False,
    )
    primary = frame.attrs.get("primary", {})
    fig.suptitle(
        f"listening-v2 | {primary.get('window_days', '?')} days | "
        f"day weight {primary.get('day_weight', '?')} | seed mean; user 95% CI",
        fontsize=10,
    )
    fig.tight_layout()
    return fig


def history_rows(bundles, config):
    import pandas as pd

    grouped = defaultdict(list)
    for bundle in bundles:
        for row in bundle["requests"]:
            if (
                row["task"] == (config.task or "discovery")
                and row["metric"] == config.metric
                and row["k"] == config.primary_k
                and row["window_days"] == config.evaluation.primary_window_days
                and row["day_weight"] in (None, config.evaluation.primary_day_weight)
                and row["value"] is not None
            ):
                grouped[(row["user_id"], row["request_id"], row["history_count"])].append(
                    row["value"]
                )
    buckets = defaultdict(list)
    for (_, _, n), xs in grouped.items():
        buckets["0–4" if n < 5 else "5–19" if n < 20 else "20–99" if n < 100 else "100+"].append(
            fmean(xs)
        )
    return pd.DataFrame(
        [
            {
                "phase": "validation",
                "task": config.task,
                "metric": config.metric,
                "window_days": config.evaluation.primary_window_days,
                "day_weight": config.evaluation.primary_day_weight,
                "k": config.primary_k,
                "history_count": name,
                "mean": fmean(xs),
                "n_users": len(xs),
            }
            for name, xs in buckets.items()
        ]
    )


def validation_rows(data, config):
    import duckdb
    import pandas as pd

    from music_recsys.listening_data import read_user_observations

    rows = []
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
        connection.read_parquet(str(data.observations_path)).create_view("observations")
        current, truths = None, set()
        for request in data.requests:
            if request.user_id != current:
                current = request.user_id
                truths = {
                    r.item_id
                    for r in read_user_observations(connection, current)
                    if r.window_days == config.evaluation.primary_window_days
                }
            eligible = set(eligible_candidates(request))
            rows.append(
                {
                    "request_id": request.request_id,
                    "user_id": request.user_id,
                    "task": request.task,
                    "history_count": len(request.history),
                    "candidate_count": len(eligible),
                    "positive_count": len(eligible & truths),
                }
            )
    return pd.DataFrame(rows)


def observation_distribution(config):
    """ペアの生行をNotebookへ展開せず、主評価窓の観測ヒストグラムを集計する。"""
    import duckdb
    import pandas as pd

    from music_recsys.listening_data import read_bundle

    _, paths = read_bundle(config.validation_dataset)
    rows = []
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
        connection.read_parquet(str(paths["observations"])).create_view("observations")
        for name in ("evaluation_play_count", "evaluation_active_days"):
            result = connection.execute(
                f"""SELECT CASE WHEN learning_play_count>0 THEN 'repeat' ELSE 'new' END task,
                CASE WHEN {name}=1 THEN '1' WHEN {name}=2 THEN '2' WHEN {name}<5 THEN '3–4' WHEN {name}<10 THEN '5–9' WHEN {name}<20 THEN '10–19' ELSE '20+' END bin,
                count(*) pairs FROM observations WHERE window_days=? GROUP BY task,bin""",
                [config.evaluation.primary_window_days],
            ).fetchall()
            rows.extend(
                {
                    "phase": "validation",
                    "window_days": config.evaluation.primary_window_days,
                    "task": task,
                    "observation": name,
                    "bin": bin,
                    "pairs": count,
                }
                for task, bin, count in result
            )
    return pd.DataFrame(rows)


def plot_observations(frame):
    import matplotlib.pyplot as plt

    if frame.empty:
        return None
    bins = ["1", "2", "3–4", "5–9", "10–19", "20+"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.5))
    for ax, metric in zip(axes, ("evaluation_play_count", "evaluation_active_days"), strict=True):
        for index, task in enumerate(("new", "repeat")):
            rows = frame[(frame.observation == metric) & (frame.task == task)].set_index("bin")
            counts = rows.pairs.reindex(bins, fill_value=0)
            ax.bar(
                [i + (index - 0.5) * 0.35 for i in range(len(bins))], counts, width=0.35, label=task
            )
        ax.set_xticks(range(len(bins)), bins)
        ax.set(
            title="Play count" if metric == "evaluation_play_count" else "Active days",
            xlabel="Observed value",
            ylabel="User-track pairs",
        )
        ax.title.set_fontsize(10)
        ax.legend()
    fig.tight_layout()
    return fig
