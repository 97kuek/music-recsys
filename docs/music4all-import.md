# Music4All-Onionの取り込み

## 対象

- 公開版[Zenodo record 15394646](https://zenodo.org/records/15394646)。配布ファイルとカラムは[取り込み元確認](music4all-import-sources.md)を参照。
- 初回は元IDの文字列順で、再生履歴に現れる楽曲100件・ユーザー100件。
- 楽曲ID・タグと重み、ユーザーIDをPostgreSQLへ、再生履歴をParquetへ保存する。曲名・アーティスト名・ユーザー属性は未取得。
- タグ未取得はNULL、取得済みの空タグ辞書は`{"tags": {}}`として区別する。
- 同一内容の重複行をまとめ、競合する内容は拒否する。

## 実行

```bash
uv run --locked --env-file .env --group db --group data music-recsys-import \
  --record data/raw/music4all-onion-v2/record.json \
  --tags data/raw/music4all-onion-v2/id_tags_dict.tsv.bz2 \
  --events data/raw/music4all-onion-v2/userid_trackid_timestamp.tsv.bz2 \
  --event-cache data/processed/music4all-onion-v2/events.parquet \
  --version-label v2 --tracks 100 --users 100 \
  --storage-root data/imports --lockfile uv.lock
```

- 前提：DockerのDBにAlembic `0003`を適用し、元ファイル・変換済みParquetと対応する`events.manifest.json`を用意する。[DB手順](local-db.md)、[全量Parquet変換・EDA](../experiments/music4all/summary.md)
- `--version-label`は明示する。APIの`metadata.version`が未設定であるため、その値から推測しない。
- 件数・抽出条件・処理版の変更は別snapshot。同じ入力・処理版ではsnapshotと同一内容の保存物を再利用する。
- 接続先は環境変数、保存先ルートは`--storage-root`で管理する。認証情報はGitに含めない。

| `--selection` | 対象 |
| --- | --- |
| `metadata`（既定） | 初回の独立した楽曲100件・ユーザー100件。履歴は登録しない |
| `history` | `--train-end 2017-03-29T00:00:00 --users 100`。Train観測ユーザーを元ID順に選び、全期間の履歴と関連曲を取り込む |
| `full` | 全ユーザー・全イベント・公開タグ内の全曲。タグのみの曲も保持 |

- `history`の`--selection-seed`を指定するとSHA-256による再現可能なユーザー抽出。未指定は元ID順。未来の活動量・正解の有無でユーザーを選ばない。
- `history_events`に使用したイベントParquetのSHA-256・保存キー・行数を固定する。
- [分割・Benchmark・Notebookの実行](music4all-benchmark.md)

## 保存・公開

| 処理 | 保証 |
| --- | --- |
| 元ファイル検証 | 配布サイズ・配布MD5 / SHA-256と実ファイルを照合し、実SHA-256も記録 |
| 入力cache | 元ファイルSHA・変換format版・PyArrow版・Parquet SHA・行数・schemaを照合。公式配布ファイルとは別のartifactとして登録 |
| 保存物 | 内容hashによる保存先。既存の保存物は上書きしない。同一filesystemではhardlink、別filesystemでは検証コピー |
| Parquet | 元ID・メタデータJSONの順とschema、writer設定を固定。楽曲・ユーザー別に保存し、hash・全行内容・ID対応を読戻しで検証 |
| 公開 | source / cacheも再検証した後、DBの構成・保存物参照・run成功・snapshot公開を一つのトランザクションで確定 |
| 再実行 | 新しいrunを記録。入力・出力が一致すれば既存snapshot・内部ID・メタデータを再利用。異なる出力を拒否 |
| 失敗 | 元ファイル/cacheのpreflight不一致はrun開始前に拒否。開始後の変換・保存・公開失敗はfailed runを記録し、途中snapshotを公開しない |
| 読込み | `load_snapshot`は公開済みsnapshotの構成に限定し、Parquetのhash・schema・内容を再検証 |

- snapshot用Parquetは共通カタログ。再生イベントを全量DBへ複製しない。
- cacheの元ファイル・変換manifestと実Parquetを入力設定で追跡する。保存先の絶対パスはfingerprintに含めない。
- Adapter / 共通処理の版は実ソースコードhashを含む。依存lock hash・実行Python / ライブラリ版・DB revisionも記録する。
- CLIと失敗履歴は例外型と集計だけを記録し、認証情報・例外本文・元ユーザーIDを出力しない。

## Pythonからの読込み

```python
from pathlib import Path
from uuid import UUID
from music_recsys.ingestion.pipeline import load_snapshot
from music_recsys.ingestion.postgres import engine_from_environment

engine = engine_from_environment()
try:
    catalog = load_snapshot(engine, Path("data/imports"), UUID("実行結果のsnapshot_id"))
finally:
    engine.dispose()
```

- `01_data.ipynb`は保存済みsnapshotを読み取る。取り込みはNotebook外のCLIで実行する。`.env`を渡して起動する場合は`uv run --env-file .env --group data --group notebook --group tracking jupyter lab`。
- 個々の楽曲・ユーザーはsnapshotを指定して読む。台帳全体から最新の内容を暗黙に選ばない。

## 実データでの検証結果

- 2026-10-12：公開元ファイル・全量event cacheを照合し、同じ条件で2回実行した。
- DBとParquetを独立に読み戻し、楽曲・ユーザーの構成とメタデータが一致することを確認した。

| 項目 | 結果 |
| --- | --- |
| 楽曲 / ユーザー | 各100件。再実行でも増加なし |
| タグ未取得 / 空タグ | 0曲 / 14曲 |
| タグ全量の検証 | 109,269行、108,286元ID。同一内容の重複983行を記録 |
| snapshot / 取り込み履歴 | 公開snapshot 1件を再利用、成功runは2件 |
| 保存物 | 6件：元ファイル2件、cacheと変換manifest、楽曲・ユーザーParquet |
| 大規模な元ファイル | 内容hash先と元ファイルのinode一致を確認し、物理的な複製を回避 |

- 合成入力と隔離DBで、競合タグ・保存物改変・異なる出力・同時再実行・公開失敗を検証した。通常DBの実データはこの失敗検証で変更していない。
