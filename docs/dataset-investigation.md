# データセット方針

| データセット | 用途・利用したい情報 |
| --- | --- |
| Music4All-Onion | 推薦モデルの検証。内容ベース・CF・行列分解・Hybrid。ジャンル・音響・歌詞関連特徴 |
| TalkPlayData | LLM / Agentの追加効果。複数ターン会話、ユーザープロファイル、楽曲名・アーティスト・タグ、楽曲・ユーザーEmbedding、推薦精度・説明の評価 |

## Music4All-Onion

- 過去の履歴から今後聴きそうな曲を推薦する。
- 既聴曲を除外し、まだ聴いていない曲を推薦する。
- 全体の時系列分割とWarm Startを初期案とする。
- train / validation / testは8:1:1を初期案とし、実際の日付は分布を見て決める。
- v2の時刻付きイベントを取得・集計済み。取得方法は [データREADME](../data/README.md)、実集計と分割候補は [調査結果](../experiments/music4all/summary.md) を参照。

## TalkPlayData

- RecSys Challenge 2026版を選択。
- 公式のデータ区分を利用する。
- 推薦時に会話の未来ターンを渡さない。
- 本体の取得・検証は今後実施する。

## 未確定

- Music4Allの分割境界、時刻不正値の扱い、test再学習、最小履歴量、初期評価の規模。
- TalkPlayの具体的な入力形式と評価基盤への接続。
