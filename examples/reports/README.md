# Notebook用の保存済み結果

| ファイル | 内容 |
| --- | --- |
| `toy-popularity.json` | 合成データ`toy-music-v1`、Popularity、K=2の実行済み結果 |

- 03はこの結果を読み込むだけで実行できる。Notebook内で実験を再実行しない。
- 入力は`examples/toy/dataset.json`。ユーザー・楽曲IDは合成データ。
- 時間は保存時の実測値。実データの精度・速度を示す結果ではない。
- 再生成はCLIで実行し、表示された保存先の`results.json`をコピーする。

```bash
uv run --locked music-recsys-benchmark \
  --config configs/toy.toml --no-tracking --output experiments/toy/runs
```
