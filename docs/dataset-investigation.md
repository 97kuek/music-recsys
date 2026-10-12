# データセット方針

| データセット | 用途・利用したい情報 |
| --- | --- |
| Music4All-Onion | 推薦モデルの検証。内容ベース・CF・行列分解・Hybrid。ジャンル・音響・歌詞関連特徴 |
| TalkPlayData | LLM / Agentの追加効果。複数ターン会話、ユーザープロファイル、楽曲名・アーティスト・タグ、楽曲・ユーザーEmbedding、推薦精度・説明の評価 |

## Music4All-Onion

- 過去の履歴から今後聴きそうな曲を推薦する。
- 既聴曲を除外し、まだ聴いていない曲を推薦する。
- 初期評価は全体の時系列分割とWarm Start。境界は2017-03-29 / 2018-09-21とし、TestではTrain＋Validationで再学習する。
- Trainに1件以上の履歴があるユーザーを元ID順に100人選び、全期間の履歴と関連曲を保持する。人数・抽出seedを変える確認はValidationで行う。
- 配布時刻をtimezone-naiveのまま扱い、1970年の1件も保持する。[評価条件](evaluation-protocol.md#music4allの初回実データ検証)
- v2の時刻付きイベントを取得・全量集計済み。[取得手順](../data/README.md)、[全量集計](../experiments/music4all/summary.md)、[分割・評価の実行](music4all-benchmark.md)を参照。

## TalkPlayData

- RecSys Challenge 2026版を選択。
- 公式のデータ区分を利用する。
- 推薦時に会話の未来ターンを渡さない。
- 本体の取得・検証は今後実施する。

## 未確定

- 研究全体の最終評価条件・比較モデル。Music4Allの全量推薦性能は未評価。
- TalkPlayの具体的な入力形式と評価基盤への接続。
