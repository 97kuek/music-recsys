# データ取得・加工

## Music4All-Onion v2

| 項目 | 記録 |
| --- | --- |
| 出典 | [Zenodo 15394646](https://zenodo.org/records/15394646)、v2、2025-05-13公開 |
| 取得ファイル | `userid_trackid_timestamp.tsv.bz2` |
| 公式サイズ | 2,211,449,511 bytes |
| 公式MD5 | `dfe82201036765f7463e6f3ce3d0f991` |
| 取得・照合 | 2026-10-12、サイズ・MD5一致 |
| 配布メタデータのライセンス | `cc-by-4.0`。取得時の`record.json`をraw配下に保存 |
| 列 | `user_id`, `track_id`, `timestamp` |
| 時刻 | 秒までの日時文字列。タイムゾーンは未確認、配布表記のまま扱う |
| 未取得 | 楽曲特徴、TalkPlay本体 |

```bash
mkdir -p data/raw/music4all-onion-v2
curl --location --fail --retry 3 --continue-at - \
  --output data/raw/music4all-onion-v2/userid_trackid_timestamp.tsv.bz2.part \
  'https://zenodo.org/records/15394646/files/userid_trackid_timestamp.tsv.bz2?download=1'
md5 -q data/raw/music4all-onion-v2/userid_trackid_timestamp.tsv.bz2.part
wc -c < data/raw/music4all-onion-v2/userid_trackid_timestamp.tsv.bz2.part
```

- サイズ・MD5一致を確認してから、末尾の`.part`を取り除く。
- データ本体・Parquet・中間データはGit管理外。

## 全量集計

```bash
uv run --locked --group data music-recsys-profile \
  --source data/raw/music4all-onion-v2/userid_trackid_timestamp.tsv.bz2 \
  --parquet data/processed/music4all-onion-v2/events.parquet \
  --output experiments/music4all/eda/runs
```

- PyArrowのバッチ読込とDuckDBでParquet化し、日別件数・履歴量・分割候補・未聴正解対象数を集計する。
- 元ファイル・ParquetのSHA-256を保存し、キャッシュ再利用時に照合する。
- DuckDB設定：メモリ上限2GB、2スレッド、processed配下に一時領域。これはプロセス全体のメモリ上限ではない。
- 出力：`runs/<実行ID>/profile.json`、`daily_counts.csv`、`summary.md`。
- 指定境界を調べる場合：`--train-end YYYY-MM-DD --validation-end YYYY-MM-DD`。
- [調査結果](../experiments/music4all/summary.md) に共有用集計を保存。
- [Notebook](../notebooks/02_music4all_data_profile.ipynb) は保存済み`experiments/music4all/eda/profile.json`を読む。別のJSONは環境変数`MUSIC_RECSYS_PROFILE`に絶対パスを指定する。
