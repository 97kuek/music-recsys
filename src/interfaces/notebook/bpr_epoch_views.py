"""One-metric figures and a local dashboard for observed BPR epoch endpoints."""

import html

METRICS = {
    ("discovery", "binary_ndcg"): ("ndcg", "Binary NDCG"),
    ("discovery", "recall"): ("recall", "Recall"),
    ("retention_proxy", "graded_ndcg"): ("graded_new", "Graded NDCG · New"),
    ("repeat", "graded_ndcg"): ("graded_repeat", "Graded NDCG · Repeat"),
    ("repeat", "repeat_recall"): ("repeat_recall", "Repeat Recall"),
    **{
        (t, m): (f"{m}_{t}", f"{m.replace('_', ' ').title()} · {t}")
        for t in ("new", "repeat")
        for m in ("ild", "catalog_coverage", "novelty")
    },
}
COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7"]


def best_epoch(summary):
    return int(
        summary.sort_values(
            ["binary_ndcg_mean", "training_seconds_mean", "epochs"], ascending=[False, True, True]
        )
        .iloc[0]
        .epochs
    )


def make_figures(points, curves, summary, *, factors, window_days, k):
    import matplotlib.pyplot as plt

    result = {}
    seeds = sorted(points.seed.unique())
    caption = "Mean ± sample SD across 3 seeds (ddof=1); not a confidence interval"
    endpoint = "Independent runs · measured endpoints only · dashed lines are visual guides"

    def canvas(title, xlabel, ylabel, footer):
        fig, ax = plt.subplots(figsize=(9, 6.5))
        fig.subplots_adjust(left=0.14, right=0.96, top=0.79, bottom=0.2)
        fig.patch.set_facecolor("white")
        fig.suptitle(title, x=0.14, y=0.96, ha="left", fontsize=19, fontweight="bold")
        fig.text(
            0.14,
            0.87,
            f"BPR · {factors} factors · Validation {window_days} days · K={k}",
            fontsize=11,
            color="#526174",
        )
        ax.set(xlabel=xlabel, ylabel=ylabel)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.18)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        ax.margins(x=0.08, y=0.18)
        fig.text(0.14, 0.06, footer, fontsize=8.5, color="#526174")
        return fig, ax

    for metric, label in [("binary_ndcg", "Binary NDCG"), ("recall", "Recall")]:
        fig, ax = canvas(
            f"Epochs vs Validation {label}@{k}",
            "Epochs",
            f"Validation {label}@{k}",
            caption + "\n" + endpoint,
        )
        ax.errorbar(
            summary.epochs,
            summary[f"{metric}_mean"],
            yerr=summary[f"{metric}_std"],
            fmt="o--",
            capsize=6,
            color=COLORS[0],
            linewidth=2,
        )
        ax.set_xticks(summary.epochs)
        result[f"epoch_{'ndcg' if metric == 'binary_ndcg' else metric}.png"] = fig
    fig, ax = canvas(
        "Seed variation across epoch budgets", "Epochs", f"Validation Binary NDCG@{k}", endpoint
    )
    for i, seed in enumerate(seeds):
        group = points[points.seed == seed].sort_values("epochs")
        ax.plot(
            group.epochs,
            group.binary_ndcg,
            marker=["o", "s", "^"][i],
            linestyle=["--", "-.", ":"][i],
            color=COLORS[i],
            label=f"Seed {seed}",
            linewidth=2,
        )
    ax.set_xticks(summary.epochs)
    ax.legend(frameon=False)
    result["epoch_seeds.png"] = fig
    fig, ax = canvas(
        "Training cost vs recommendation accuracy",
        "Training time (seconds)",
        f"Validation Binary NDCG@{k}",
        caption + "\nTraining time includes load/setup/fit/transfer; evaluation excluded",
    )
    for i, row in enumerate(summary.itertuples()):
        group = points[points.epochs == row.epochs]
        ax.scatter(group.training_seconds, group.binary_ndcg, color=COLORS[i], alpha=0.25, s=30)
        ax.errorbar(
            row.training_seconds_mean,
            row.binary_ndcg_mean,
            xerr=row.training_seconds_std,
            yerr=row.binary_ndcg_std,
            fmt="o",
            capsize=5,
            color=COLORS[i],
            label=f"{row.epochs} epochs",
            markersize=8,
        )
    ax.legend(frameon=False)
    result["epoch_time.png"] = fig
    for (task, metric), (short, label) in METRICS.items():
        subset = curves[(curves.task == task) & (curves.metric == metric)].dropna(subset=["mean"])
        if subset.empty:
            continue
        fig, ax = canvas(
            f"K sensitivity: {label}",
            "Recommended tracks (K)",
            label,
            caption + "\nAll saved K=2–20 values; same split and candidate rules",
        )
        for i, (epoch, group) in enumerate(subset.groupby("epochs", sort=True)):
            stats = group.groupby("k")["mean"].agg(["mean", "std"])
            ax.plot(
                stats.index, stats["mean"], color=COLORS[i], label=f"{epoch} epochs", linewidth=2
            )
            ax.fill_between(
                stats.index,
                stats["mean"] - stats["std"],
                stats["mean"] + stats["std"],
                color=COLORS[i],
                alpha=0.1,
            )
        ax.set_xticks(range(2, 21, 2))
        ax.legend(frameon=False, fontsize=9)
        result[f"k_{short}.png"] = fig
    return result


def dashboard(summary, baselines, images, population, *, factors, window_days, k):
    selected = best_epoch(summary)
    baselines = baselines[["model", "binary_ndcg", "recall", "kind"]]
    cards = []
    captions = {
        "epoch_ndcg.png": "① Epochs × Validation Binary NDCG",
        "epoch_recall.png": "② Epochs × Validation Recall",
        "epoch_seeds.png": "Seed別の独立学習結果",
        "epoch_time.png": "⑤ 学習時間 × 推薦精度",
    }
    for name in images:
        title = captions.get(name, name.removesuffix(".png").replace("_", " "))
        cards.append(
            f'<figure><figcaption>{html.escape(title)}</figcaption><a href="{name}"><img loading="lazy" src="{name}" alt="{html.escape(title)}"></a></figure>'
        )
    display = summary[
        [
            "epochs",
            "binary_ndcg_mean",
            "binary_ndcg_std",
            "recall_mean",
            "recall_std",
            "training_seconds_mean",
            "training_seconds_std",
        ]
    ]
    return f"""<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BPR epoch comparison</title><style>
body{{margin:0;background:#f5f7fb;color:#172b45;font:16px/1.7 system-ui,sans-serif}}main{{max-width:1380px;margin:auto;padding:32px}}h1{{margin-bottom:4px}}p{{color:#506176}}a{{color:#0067a0}}.note,section,figure{{background:white;border:1px solid #dce4ee;border-radius:12px;padding:20px;margin:20px 0}}.gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,550px),1fr));gap:20px}}figure{{margin:0;padding:12px}}img{{display:block;width:100%;height:auto}}figcaption{{padding:8px;font-weight:650}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{padding:10px;border-bottom:1px solid #e4eaf1;text-align:right;white-space:nowrap}}.scroll{{overflow:auto}}nav a{{margin-right:18px}}
</style><main><h1>BPR: エポック数の比較</h1><p>{factors}因子 · seed 42 / 43 / 44 · Validation {window_days}日 · New tracks · K={k}</p>
<nav><a href="analysis.md">分析レポート</a><a href="metrics.csv">比較表CSV</a><a href="conditions.csv">Seed別CSV</a><a href="sensitivity.csv">4観点・K感度CSV</a><a href="paired.csv">対応差CSV</a><a href="baselines.csv">Baseline CSV</a></nav>
<div class="note"><strong>結論: 主指標の実測最大は{factors}因子・{selected}epoch、学習時間との比較は下表を参照</strong><p>30/100/200/500は独立した学習の終点です。同じ学習を追ったLearning Curveではありません。線の間に実測値はありません。エラーバーは3seedの標本標準偏差です。</p></div>
<section><h2>比較表</h2><div class="scroll">{display.to_html(index=False, float_format=lambda x: f"{x:.6f}", border=0)}</div></section>
<section><h2>未記録の曲線</h2><p>③ Training Lossと④学習途中のValidation NDCGは未記録です。値・グラフは補完していません。ロス低下だけから過学習を判定しません。</p></section>
<div class="gallery">{"".join(cards)}</div>
<section><h2>NDCGの評価条件とベースライン</h2><p>学習ユーザー{population["users"]:,}人のうち、90日New正解がある{population["eligible_users"]:,}人を精度集計。カタログ{population["items"]:,}曲、正解曲数は対象者平均{population["truth_mean"]:.2f}曲、候補は平均{population["candidate_mean"]:.0f}曲。Randomは一様ランダム推薦の理論期待値で、実行した実験ではありません。</p><div class="scroll">{baselines.to_html(index=False, float_format=lambda x: f"{x:.6f}", border=0, na_rep="未測定")}</div></section></main></html>"""


def analysis_report(summary, points, paired, baselines, population, audit, *, command):
    selected = best_epoch(summary)
    seed_endpoints = points.pivot(index="seed", columns="epochs", values="binary_ndcg")
    improved = int((seed_endpoints[500] > seed_endpoints[200]).sum())
    monotone = all(row.is_monotonic_increasing for _, row in seed_endpoints.iterrows())
    first, last = summary.iloc[0], summary.iloc[-1]
    mid = summary[summary.epochs == 200].iloc[0]
    hundred = summary[summary.epochs == 100].iloc[0]
    gain = (last.binary_ndcg_mean / mid.binary_ndcg_mean - 1) * 100
    time_gain = (last.training_seconds_mean / mid.training_seconds_mean - 1) * 100
    pop = baselines[baselines.model == "Popularity/users"].iloc[0]
    random = baselines[baselines.model == "Random (analytic expectation)"].iloc[0]
    table = [
        "| Epochs | NDCG@20 平均±SD | Recall@20 平均±SD | 学習秒 平均±SD |",
        "|---|---:|---:|---:|",
    ]
    for row in summary.itertuples():
        table.append(
            f"| {row.epochs} | {row.binary_ndcg_mean:.6f} ± {row.binary_ndcg_std:.6f} | {row.recall_mean:.6f} ± {row.recall_std:.6f} | {row.training_seconds_mean:.3f} ± {row.training_seconds_std:.3f} |"
        )
    pairs = [
        f"- {r['before']}→{r['after']}: NDCG差 {r['mean']:+.6f}、ユーザーbootstrap 95%区間 [{r['ci_low']:+.6f}, {r['ci_high']:+.6f}]"
        for r in paired
    ]
    baseline_table = ["| 比較対象 | NDCG@20 | Recall@20 | 種別 |", "|---|---:|---:|---|"]
    for row in baselines.itertuples():
        baseline_table.append(
            f"| {row.model} | {row.binary_ndcg:.6f} | {row.recall:.6f} | {row.kind} |"
        )
    cv = summary.binary_ndcg_std / summary.binary_ndcg_mean * 100
    return f"""# BPRエポック数の分析

## 推奨設定

- 今回の主指標はNew Binary NDCG@20、補助指標はNew Recall@20、Validation 90日
- 128因子の範囲では**{selected}epoch・学習率0.01・正則化0.01**を暫定推奨する
- 理由: 実測した4設定中でNDCG平均が最大。200→500では{improved}/3 seedのNDCGが改善した
- 500epochは200epochよりNDCGが{gain:.2f}%改善し、学習時間は{time_gain:.2f}%増加する
- 学習コストを重視する運用では200epochが候補。予算・許容精度差の基準がないため、200を唯一の最適とは決めない
- 500は探索範囲内の最大値であり、真の最適epoch・収束点を確認したものではない
- 因子数128の判断は100epochでのコスト比較に基づく暫定条件。因子数×epochの交互作用は別途比較する
- Test・設定の固定・配信モデルの切替は実行していない

## 統一条件と再利用の監査

- BPR GPU float32、implicit 0.7.2、RTX 3090、評価CUDA float64、batch=128
- factors=128、epochs=30/100/200/500、seed=42/43/44、learning_rate=.01、regularization=.01
- 入力・分割・特徴hash、全学習ユーザー・候補・正解定義、評価窓・Kを照合した
- 学習期間は2017-03-29より前、今回のValidation窓は2017-03-29以上2017-06-27未満
- 30/100のsourceは`fdc2dbbb…`、200/500は`cd79c507…`で異なる
- Git tree全体を記録hashと照合し、BPR学習・入力・順位付け・評価の{len(audit["critical_modules"])}モジュールを比較した
- 差はパッケージ/import移動と記録用lockパス、実行Runnerの条件展開・帳票。対象BPRの計算ロジックは一致した
- 同じGPU・backend・精度・依存lock・負例抽出設定を確認。GPU並列更新は同seedでもビット一致を保証しない
- 各epochは独立した学習。同seedでも長い学習の途中checkpointとの同一性は保証しない
- 学習時間は保存されたtraining_secondsで、入力読込み・初期化・学習・GPU→CPU転送を含み、評価時間は含まない
- 時間は別実行の実測であり、ホスト負荷やウォームアップを統制した専用ベンチマークではない
- 元結果・学習済みモデル・実験JSONは変更せず、解析成果物をBPR直下へ保存した

## 比較表

{chr(10).join(table)}

- SDは3seedの標本標準偏差（ddof=1）、ユーザー信頼区間・標準誤差とは異なる
- seedごとの値は[conditions.csv](conditions.csv)、4観点とK感度は[sensitivity.csv](sensitivity.csv)
- 30→500でNDCGは{(last.binary_ndcg_mean / first.binary_ndcg_mean - 1) * 100:.2f}%、Recallは{(last.recall_mean / first.recall_mean - 1) * 100:.2f}%改善した
- 100→200でNDCGは{(mid.binary_ndcg_mean / hundred.binary_ndcg_mean - 1) * 100:.2f}%改善、200→500では{gain:.2f}%と増分が小さい
- NDCG増分/追加epochは100→200の{(mid.binary_ndcg_mean - hundred.binary_ndcg_mean) / 100:.8f}から、200→500の{(last.binary_ndcg_mean - mid.binary_ndcg_mean) / 300:.8f}へ低下する
- 200付近から改善速度が落ちるが、500まで平均は上昇しているため、頭打ち・収束完了とは断定しない
- NDCGのseed間変動係数は{cv.min():.2f}〜{cv.max():.2f}%、3seedのみなので学習乱数の不確実性を十分に推定したとは言えない

## 対応のあるユーザー差

{chr(10).join(pairs)}

- 同じ{population["eligible_users"]:,}人について3seed平均を取ってからユーザーを2,000回再抽出、bootstrap seed=42
- 区間は各差の点ごとの95%区間で、多重比較補正・seed母集団の不確実性・探索後選択の補正は含まない
- 単に別々のNDCG区間の重なりだけで差を判断しない。詳細は[paired.csv](paired.csv)

## ロス・過学習・Early Stopping

- 各実験のepoch別ログには時間とskippedがあるが、Training Lossは記録されていない
- 同一学習の途中Validation NDCGも未記録。両曲線は未作成で、未測定を0や補間値で埋めていない
- 実測4終点の各seed NDCGは{"単調非減少で、悪化の兆候は見られない" if monotone else "単調増加ではないため、悪化した区間を確認する必要がある"}
- 中間epochや500超の悪化は未検証であり、過学習がないとは証明できない。学習ロスだけでも過学習を断定しない
- Early Stoppingの必要性・停止位置は今回の終点比較だけでは決定できない
- 次に導入するなら、同一学習のValidationを固定間隔で測り、最良checkpointを保持する
- min_delta・patience・評価間隔と追加の評価コストを事前に決め、未指定の閾値で200epoch停止を正当化しない
- 追加の学習・中間計測は実施していない

## NDCGの絶対値と評価母集団

- 学習ユーザー{population["users"]:,}人、学習曲{population["items"]:,}曲、学習再生{population["training_events"]:,}件
- New正解保有者{population["eligible_users"]:,}人（全体の{population["eligible_users"] / population["users"] * 100:.2f}%）、正解なし{population["excluded_users"]:,}人を精度の分母から除外
- 正解はユニーク{population["truth_tracks"]:,} user-track組。対象者あたり平均{population["truth_mean"]:.3f}曲、中央値{population["truth_median"]:.0f}、90%点{population["truth_p90"]:.0f}、最大{population["truth_max"]}
- 全学習ユーザーを分母にすると正解曲数平均は{population["truth_mean_all_users"]:.3f}曲。精度集計の分母と混同しない
- New候補は学習カタログから本人の学習既聴曲を除外。対象者の候補平均{population["candidate_mean"]:.3f}曲、範囲{population["candidate_min"]:,}〜{population["candidate_max"]:,}曲
- 1曲あたりValidation再生回数は平均{population["play_count"]["mean"]:.3f}・中央値{population["play_count"]["median"]:.0f}・90%点{population["play_count"]["p90"]:.0f}、再生日数は平均{population["active_days"]["mean"]:.3f}・中央値{population["active_days"]["median"]:.0f}・90%点{population["active_days"]["p90"]:.0f}
- Binary NDCGは二値正解とlog割引、IDCGはmin(正解数,K)の理想順位。ユーザーを等重みで平均する
- Recallは上位K内の的中数/その人の全New正解数。NDCGの0.026を的中率2.6%と読み替えない
- 正解はログで観測した再生で、未観測曲を嫌いな曲とみなさない。観測窓末まで全員を追跡できた保証はない
- 学習時点で未知の曲はwarm評価から除外。90日窓のcold-item再生除外は340,050件で、全カタログ・cold-start性能とは異なる

## リークと採点の監査

- モデルのtraining_refとTrain入力hashを照合。学習APIにはlearning.parquetのみを渡し、未来の正解はEvaluatorが読むコードを確認した
- 12条件すべてで保存推薦の全学習ユーザー・候補数・ユニークID・既聴除外・既知カタログを検査した
- 保存順位からCPU参照式でNDCG/Recallを再計算し、保存集計との一致を確認した
- 監査は保存物と実行コードの整合性の確認。原始ログの全timestampから独立に分割を再生成したものではない
- Testは読まず、Validation結果だけで推奨した

## ベースライン

{chr(10).join(baseline_table)}

- Popularity・ItemCFは同じ入力・対象者・候補・K・窓の保存結果を再利用。CPUとGPUの実行差があるため、時間の方式間比較は行わない
- 500epochのNDCGはPopularity/usersの{last.binary_ndcg_mean / pop.binary_ndcg:.2f}倍、一様Random期待値の{last.binary_ndcg_mean / random.binary_ndcg:.2f}倍
- Randomは各ユーザーの候補から重複なく一様抽出した場合の**理論期待値**で、実測値・3seed平均ではない
- Randomの式: E[Recall@K]=K/C、E[NDCG@K]=(m/C)×sum(discount[1:K])/sum(discount[1:min(m,K)])を正解保有者で平均
- Cは本人のNew候補数、mはNew正解数。平均mと平均Cの比で代用していない
- ItemCF/Cosine20は今回のBPRより高い。BPRが既存推薦器全体で最良とは結論しない

## 4観点と次の実験案

- [一覧ダッシュボード](dashboard.html)に新規発見、継続的な嗜好、多様性／発見性、効率性とK=2〜20の図を掲載
- 30→500でRepeat Recall@20は{first.repeat_recall_mean:.6f}→{last.repeat_recall_mean:.6f}、既聴曲Graded NDCGは{first.graded_repeat_mean:.6f}→{last.graded_repeat_mean:.6f}と改善した
- New ILD@20は{first.ild_new_mean:.6f}→{last.ild_new_mean:.6f}と低下する一方、Coverageは{first.catalog_coverage_new_mean:.6f}→{last.catalog_coverage_new_mean:.6f}、Noveltyは{first.novelty_new_mean:.6f}→{last.novelty_new_mean:.6f}と上昇した
- 多様性・発見性の各指標は同じ傾向ではない。精度の改善だけで推薦全体の優位性を断定しない
- 推薦レイテンシはGPUバッチを要求数で按分した値であり、Macやブラウザの単発応答速度を表さない
- K=20のRepeat Recall・Graded NDCG・ILD・Coverage・Novelty・latencyも[比較表CSV](metrics.csv)に保存
- API料金・token・LLM/tool callは未測定をnullのまま保持し、全値未測定の図は作らない
- まず既存256因子の200/500epochも比較し、100epochでの因子数の頭打ちが長期学習でも維持されるか確認する
- 将来の追加実験候補は200〜500付近の密な測定・500超のValidation終点、学習率/正則化の検証と追加seed
- 追加実験の前に精度差の許容範囲・計算予算を定義し、閾値を結果に合わせて後付けしない

## 再現と保存先

```bash
{command}
```

- 上記は保存結果の読込み・監査・描画のみ。元の学習は[実行フロー](../../../README.md#主なcli)を参照
- 入力設定はconfigs/bpr-30epoch.json・bpr-100epoch.json・bpr-200epoch.json・bpr-500epoch.json、共通条件はconfigs/common.json
- 共通設定selection.k=10は変更せず、保存済みK=20を今回の選択指標として明示集計した
- 元結果は30epoch/・100epoch/・200epoch/・500epoch/配下のf128-s42/・f128-s43/・f128-s44/
- 解析条件・全hash・監査結果はconfig.json / result.json、出力CSV・PNG・dashboard.html・analysis.mdはこのフォルダ直下
- 保存済み解析は上書きしない。再作成は新しい出力先を明示する

```mermaid
flowchart LR
  A[既存Validation結果・モデル・Parquet] --> B[hash・設定・ソースの照合]
  B --> C[保存順位の採点監査・seed集計・対応差]
  C --> D[CSV・単一指標PNG・Dashboard・分析]
```
"""
