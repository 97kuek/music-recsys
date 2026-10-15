# ItemCFベースライン

`itemcf`は、学習期間のユーザー×楽曲を二値化したItem-based Collaborative Filtering。繰り返し再生は類似度の重みを増やさない。再生回数を使うモデルは別条件として追加する。

曲iを聴いた学習ユーザー集合をU(i)とし、Cosineは`|U(i)∩U(j)| / sqrt(|U(i)|*|U(j)|)`、Jaccardは`|U(i)∩U(j)| / |U(i)∪U(j)|`。自己類似度を除き、各曲について正の類似度の上位`neighbors`曲を学習時に保存する。近傍の同点はID昇順。全曲×全曲の密行列は作らないが、共起集計の時間は履歴の密度に依存する。

推薦スコアは、本人の各履歴曲の保存近傍に候補曲が含まれる場合の類似度合計。候補ごとに近傍を選び直さない。スコア降順、同点は学習再生回数降順・ID昇順。根拠がない候補のスコアは0で、Popularityにより補完する。03では`fallback="popularity"`と理由を表示する。選択したデータ・seedは保存するが、このモデル自体は決定的である。

参考：[Sarwar et al., Item-Based Collaborative Filtering Recommendation Algorithms](https://files.grouplens.org/papers/www10_sarwar.pdf)。本実装の二値化・近傍の切り方・補完規則は上記の研究用ベースライン仕様に固定する。

## 操作

`configs/workflow-toy-itemcf.toml`は近傍数×類似度の2変数Grid Searchの例。01・02・03とCLIで同じファイルを使う。

```bash
export MUSIC_RECSYS_EXPERIMENT_CONFIG="$PWD/configs/workflow-toy-itemcf.toml"
uv run --locked --group data --group tracking python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" smoke
uv run --locked --group data --group tracking python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" search
```

Stage Aでは`search.stage="A"`、`model.parameters.similarity="cosine"`を指定し、gridは`neighbors`だけにする。固定値とgridで同じキーを重複させない。Stage C・設定選択・03の確認・固定・最終Testは[共通の手順](experiment-workflow.md)に従う。合成データの探索結果をMusic4Allの最適値とみなさない。

従来のBenchmark CLIでも`benchmark.model="itemcf"`と`[benchmark.parameters] neighbors=50 / similarity="cosine"`を指定できる。探索の設定は共通TOMLで管理し、単発Benchmarkの設定と二重に編集しない。
