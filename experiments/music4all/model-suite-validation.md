# 従来7モデルの実データValidation診断

2026-10-15実施。実装・保存・再利用の確認用であり、研究上の最良モデルを選んだ結果ではない。主タスク・主指標は未指定のままとし、選択・固定・Test評価は実行していない。設定順で掲載し、性能による順位付けをしていない。

## 固定した条件

| 条件 | 値 |
| --- | --- |
| データ | music4all-onion、既存100ユーザー cohort |
| 学習 | 83,745イベント、12,664曲 |
| Snapshot | `3b3115c3-5958-44fe-9705-31f647db8ac2` |
| Split | `7d50f6c9-6835-4919-be91-1293ae0a6f1d` |
| 学習終了 / Validation終了 | 2017-03-29 / 2018-09-21（元時刻のタイムゾーン未検証） |
| 評価 | listening-v2、窓30 / 90 / 180日、K=1〜20、λ=0 / .5 / 1 / 2 |
| この表 | 90日、K=10、段階的評価λ=1、seed 0 / 42の平均 |
| 90日の正解 | new: 10ユーザー・130ペア、repeat: 12ユーザー・354ペア |
| 特徴 | Snapshotの重み付きタグ。学習曲の12,485曲に非ゼロ特徴、179曲は欠損またはゼロ |
| 探索 | Stage A。Popularity 1設定、他6モデル各2設定、各設定2seed（本探索26run） |
| Smoke / 再利用 | 各モデル1回Smoke成功（別7run）、本探索の全26run再利用成功 |
| MLflow | 本探索26runとSmoke 7runの成功を確認。以前の診断runも別条件として保持 |

## Accuracy（Validation）

Binaryは確定済みの二値正解。Gradedは再生回数・日数を用いる暫定ルールの感度確認で、確定した研究上の関連度ではない。NDCGの括弧内は2seedの母標準偏差であり、95%信頼区間ではない。ユーザー単位95% bootstrap CIは02の各trial表示で確認できる。

| ID | モデル / 可変設定 | New Binary NDCG | New Recall | New Graded NDCG | Repeat Graded NDCG | Repeat Recall |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| S01 | `popularity / {}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.1447 (±0.0000) | 0.0363 |
| S02 | `itemcf / {"neighbors": 20, "similarity": "cosine"}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0282 (±0.0000) | 0.0131 |
| S03 | `itemcf / {"neighbors": 50, "similarity": "cosine"}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0321 (±0.0000) | 0.0131 |
| S04 | `usercf / {"neighbors": 10, "similarity": "cosine"}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0353 (±0.0000) | 0.0062 |
| S05 | `usercf / {"neighbors": 30, "similarity": "cosine"}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0245 (±0.0000) | 0.0039 |
| S06 | `als / {"alpha": 10.0, "factors": 8, "iterations": 5, "regularization": 0.1}` | 0.0142 (±0.0142) | 0.0026 | 0.0068 | 0.0255 (±0.0222) | 0.0131 |
| S07 | `als / {"alpha": 10.0, "factors": 16, "iterations": 5, "regularization": 0.1}` | 0.0043 (±0.0043) | 0.0013 | 0.0033 | 0.0105 (±0.0007) | 0.0045 |
| S08 | `bpr / {"factors": 8, "iterations": 5, "learning_rate": 0.05, "regularization": 0.01}` | 0.0035 (±0.0035) | 0.0023 | 0.0024 | 0.0307 (±0.0038) | 0.0068 |
| S09 | `bpr / {"factors": 16, "iterations": 5, "learning_rate": 0.05, "regularization": 0.01}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0564 (±0.0176) | 0.0495 |
| S10 | `content / {"aggregation": "binary", "use_idf": false}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0321 (±0.0000) | 0.0103 |
| S11 | `content / {"aggregation": "binary", "use_idf": true}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0519 (±0.0000) | 0.0165 |
| S12 | `hybrid / {"cf_weight": 0.25, "neighbors": 20, "pool_size": 100, "similarity": "cosine"}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0229 (±0.0000) | 0.0143 |
| S13 | `hybrid / {"cf_weight": 0.75, "neighbors": 20, "pool_size": 100, "similarity": "cosine"}` | 0.0000 (±0.0000) | 0.0000 | 0.0000 | 0.0361 (±0.0000) | 0.0143 |

## 多様性・発見性（Validation、K=10）

正解の有無によらず各タスク100要求を母集団とする。ILDは全有効推薦曲に非ゼロの特徴があり、2曲以上ある要求だけを集計する。表の人数はseed平均。Coverageは各seedで候補集合の和集合を分母として計算した値の平均である。窓・λによる複製測定はしていない。

| ID | task | ILD | ILD人数 / 100 | Novelty（bits） | Coverage | 分母曲数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| S01 | new | 0.8348 | 100 | 4.8524 | 0.0013 | 12664 |
| S01 | repeat | 0.6977 | 96 | 3.9559 | 0.0409 | 12664 |
| S02 | new | 0.7979 | 86 | 5.2675 | 0.0494 | 12664 |
| S02 | repeat | 0.7756 | 89 | 5.4972 | 0.0753 | 12664 |
| S03 | new | 0.7887 | 87 | 5.3858 | 0.0501 | 12664 |
| S03 | repeat | 0.7667 | 89 | 5.4708 | 0.0754 | 12664 |
| S04 | new | 0.6741 | 100 | 3.5226 | 0.0331 | 12664 |
| S04 | repeat | 0.5888 | 99 | 3.8635 | 0.0430 | 12664 |
| S05 | new | 0.6661 | 100 | 3.2069 | 0.0210 | 12664 |
| S05 | repeat | 0.6048 | 99 | 3.7203 | 0.0378 | 12664 |
| S06 | new | 0.7880 | 97.5 | 3.9752 | 0.0297 | 12664 |
| S06 | repeat | 0.6873 | 94 | 4.2756 | 0.0558 | 12664 |
| S07 | new | 0.7832 | 96.5 | 3.8610 | 0.0373 | 12664 |
| S07 | repeat | 0.6875 | 95 | 4.3097 | 0.0569 | 12664 |
| S08 | new | 0.8149 | 92.5 | 4.7135 | 0.0434 | 12664 |
| S08 | repeat | 0.7333 | 97 | 4.5369 | 0.0592 | 12664 |
| S09 | new | 0.8619 | 89 | 4.9425 | 0.0597 | 12664 |
| S09 | repeat | 0.7387 | 95.5 | 4.6213 | 0.0630 | 12664 |
| S10 | new | 0.2290 | 100 | 5.1835 | 0.0475 | 12664 |
| S10 | repeat | 0.3328 | 99 | 4.7525 | 0.0643 | 12664 |
| S11 | new | 0.2479 | 100 | 5.1193 | 0.0518 | 12664 |
| S11 | repeat | 0.3394 | 99 | 4.7648 | 0.0649 | 12664 |
| S12 | new | 0.2522 | 100 | 5.1048 | 0.0524 | 12664 |
| S12 | repeat | 0.3552 | 99 | 4.8567 | 0.0669 | 12664 |
| S13 | new | 0.7620 | 88 | 5.2244 | 0.0509 | 12664 |
| S13 | repeat | 0.6729 | 93 | 5.4444 | 0.0749 | 12664 |

ILDの除外理由は要求別の`ild_exclusion_reasons`と集計の`ild_exclusion_counts`に保存する。特徴欠損・ゼロノルム・有効曲不足・生成失敗などを区別できる。Catalog Coverageの空分母とNoveltyの除外理由も保持する。

## 効率（秒、Validation）

Trainingは共有特徴の読込み・検証とモデル生成を含む。Latency P50/P95は各seedの200要求（new / repeat）の分位点を求め、その2seed平均を掲載。trial時間は採点・保存・MLflow記録も含む。各モデルを順に実行した端末上の参考値。他の検証処理と同時実行の区間があり、専用環境での速度比較ではない。

| ID | Training平均 | Latency P50平均 | Latency P95平均 | trial平均 | 2seed合計 |
| --- | ---: | ---: | ---: | ---: | ---: |
| S01 | 0.7197 | 0.0014 | 0.0031 | 4.7597 | 9.5194 |
| S02 | 4.2305 | 0.0036 | 0.0069 | 9.4894 | 18.9788 |
| S03 | 5.5625 | 0.0058 | 0.0115 | 10.7039 | 21.4077 |
| S04 | 1.2070 | 0.0047 | 0.0102 | 8.3319 | 16.6637 |
| S05 | 1.4138 | 0.0099 | 0.0212 | 10.0953 | 20.1906 |
| S06 | 2.4176 | 0.0101 | 0.0258 | 11.4097 | 22.8193 |
| S07 | 2.7112 | 0.0092 | 0.0248 | 10.0279 | 20.0559 |
| S08 | 1.5700 | 0.0060 | 0.0151 | 6.6500 | 13.3001 |
| S09 | 1.7946 | 0.0067 | 0.0156 | 6.4771 | 12.9542 |
| S10 | 1.2882 | 0.0352 | 0.1054 | 13.7365 | 27.4730 |
| S11 | 1.3154 | 0.0322 | 0.0700 | 11.2726 | 22.5453 |
| S12 | 3.8719 | 0.0391 | 0.0785 | 15.2742 | 30.5485 |
| S13 | 3.9535 | 0.0382 | 0.0733 | 14.2784 | 28.5567 |

Peak Memory・Input / Output Tokens・API Cost・LLM Calls・Tool Callsは未測定（保存値null）。LLM・Agentは今回の対象外。0による補完はしていない。

## 再現と保存

```bash
.venv/bin/python -m music_recsys.experiment_search --config configs/workflow-music4all-usercf.toml smoke
.venv/bin/python -m music_recsys.experiment_search --config configs/workflow-music4all-usercf.toml search
# 同じコマンドの再実行で成功済みrunを再利用
export MUSIC_RECSYS_EXPERIMENT_CONFIG="$PWD/configs/workflow-music4all-usercf.toml"
# notebooks/02_analysis.ipynbを開き、診断trialを確認
```

他モデルは`configs/workflow-music4all-{popularity,itemcf,als,bpr,content,hybrid}.toml`。export手順は[実装計画](../../docs/superpowers/plans/2026-10-15-traditional-model-suite.md#task-7-実データvalidation全体検証文書)と[モデルの使用手順](../../docs/models.md)を参照。既存出力は上書きせず、コード変更時は新しいoutputへ保存する。

- ローカル集計JSON: `data/experiments/model-suite/traditional-final/aggregate.json`（全窓・K・λ、seed差、trial ID / MLflow run IDを含む。生履歴は含まない）
- 個別run: `data/experiments/model-suite/traditional-final/<model>/study.json`と`trials/`
- MLflow: `sqlite:///data/experiments/model-suite/tracking.db`、experiment `music4all-model-suite-validation`
- 実装commit: `9936269765d79d5cf639fb4b8654257997965e98`（この結果記録の文書commit以前）

- Runtime: `{"duckdb": "1.5.6", "matplotlib": "3.11.2", "mlflow": "3.17.0", "numpy": "2.5.4", "platform": "macOS-27.0.1-arm64-arm-64bit", "pyarrow": "25.0.1", "python": "3.12.13"}`
- source_sha256: `687d173ba1479658c0f68c7199d20f06e1d67207f9d9b9c3c35f6a47baace0db`
- dependency_lock_sha256: `aeba0d57586e9fd49e93e412d41be639e846f6c16cbaf0f508f6142a723a5770`
- validation_sha256: `e816fc6890ef06226e0f1de12480c18a322a26aafb3d96b78415fafc34308208`
- features_sha256: `254b7c7b97b9fa612285970268abffde8cf606b8625c20c0e228d4bffc99f226`

## 観察と次の作業

この小規模cohortは90日で正解を持つユーザーが10 / 12人に限られる。新規曲の指標が0の設定も、出力失敗や未測定とは区別する。モデルの優劣や大規模データでの最適値へ一般化しない。タグは保存Snapshot由来だが、学習終了以前に利用できた特徴であることは証明していない。実験は静的タグによる技術検証として扱う。

研究上の主タスク・主指標とGradedの関連度を事前に決め、別の実験名・outputでValidation探索を開始する。次に評価可能人数を確保したデータ条件を01で確認し、02で候補設定を選び、03でValidation推薦を観察してから固定・最終Testへ進む。この診断結果を見て実施済みTestを探索へ戻すことはない。
