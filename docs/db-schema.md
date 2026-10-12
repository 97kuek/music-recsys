# 初回取り込みのテーブル定義

- 対象：Music4All-Onionの楽曲ID・タグと重み、ユーザーIDの小規模取り込み。
- 方針は[DB設計の合意事項](db-design.md)、実カラムは[配布ファイルの確認](music4all-import-sources.md)を参照。
- Alembic revision `0002`に実装。[migration](../migrations/versions/0002_ingestion_schema.py)と[SQL定義](../migrations/sql/)、[Adapter・実ファイル取り込み](music4all-import.md)を参照。

## 共通定義

| 表記 | 定義 |
| --- | --- |
| `id` | 記載された表では`UUID`の主キー。新規行にUUID v4を発行し、再利用対象が存在する場合は既存IDを使用 |
| `dataset_id` | `UUID NOT NULL`。`datasets.id`への外部キー |
| `created_at` | `TIMESTAMPTZ NOT NULL`。記録作成時刻 |
| `sha256`・`*_sha256`・`*_fingerprint` | `TEXT NOT NULL`。小文字16進数64文字 |
| `?` | NULLを許可。記載のないカラムはNOT NULL |
| FK・UQ・PK | 外部キー・一意制約・主キー |

- 日時はDBの記録時刻。タイムゾーン未確認の再生時刻をUTCと解釈する規則は追加しない。
- 元IDは文字列として保持し、先頭ゼロ・大文字小文字を変更しない。空文字・空白だけのIDを拒否する。
- 外部キーの削除動作は`RESTRICT`。履歴・公開済み参照を連鎖削除しない。
- 所有するデータセットを示す`dataset_id`も外部キーに含める。UUIDが一意でも所属の整合性は別に検証する。

## 出典・配布版・保存物

| テーブル | カラム | キー・制約 |
| --- | --- | --- |
| `datasets` | `id`, `key TEXT`, `created_at` | UQ(`key`)。`key`は空白だけを許可せず、作成後は固定 |
| `dataset_versions` | `id`, `dataset_id`, `version_label TEXT`, `source_ref TEXT`, `source_manifest JSONB`, `manifest_sha256 TEXT`, `created_at` | UQ(`dataset_id`, `source_ref`, `manifest_sha256`)。manifestはオブジェクト |
| `data_artifacts` | `id`, `dataset_id`, `format TEXT`, `storage_key TEXT`, `sha256`, `byte_size BIGINT`, `row_count BIGINT?`, `verified_at TIMESTAMPTZ`, `created_at` | UQ(`dataset_id`, `sha256`)・UQ(`storage_key`)。サイズ・行数は0以上。Parquetは行数必須 |
| `source_files` | `id`, `dataset_id`, `dataset_version_id UUID`, `logical_name TEXT`, `source_url TEXT`, `expected_checksum_algorithm TEXT?`, `expected_checksum TEXT?`, `artifact_id UUID`, `created_at` | UQ(`dataset_version_id`, `logical_name`)。期待チェックサムの方式・値は両方NULLか両方設定。版・保存物の所属一致をFKで保証 |

- 配布版のmanifestは配布ファイル名・サイズ・配布チェックサムを固定し、閲覧数などの変動するAPI情報をhashに含めない。
- source fileの登録時に、ファイル名・サイズ・配布チェックサムと検証済み保存物が、その版のmanifestに一致することを確認する。manifestにないファイルや不一致を拒否し、所属FKだけで版の一致とみなさない。
- 同じ配布ラベルでもmanifestが変わった場合は別の版として登録し、旧版は変更しない。
- `data_artifacts`は保存・読戻し・チェックサム検証が完了したファイルだけを登録する。
- 同じデータセット内で同じ内容のファイルは既存の保存物を再利用する。形式・サイズなどが食い違う場合は拒否する。
- `storage_key`は環境側の保存ルートからの相対キー。データセット名・内容hashで保存先を区別し、既存ファイルを上書きしない。絶対パス・親ディレクトリへの移動を含めず、研究室サーバーへの移行では保存ルートを変更する。
- 配布版・source file・検証済み保存物の内容と参照は、登録後のUPDATE / DELETEを禁止する。

## 楽曲・ユーザー・メタデータ本体

| テーブル | カラム | キー・制約 |
| --- | --- | --- |
| `tracks` | `id`, `dataset_id`, `source_id TEXT`, `created_at` | UQ(`dataset_id`, `source_id`)。UPDATE / DELETE禁止 |
| `users` | `id`, `dataset_id`, `source_id TEXT`, `created_at` | UQ(`dataset_id`, `source_id`)。UPDATE / DELETE禁止 |
| `track_metadata` | `id`, `dataset_id`, `track_id UUID`, `payload JSONB`, `content_sha256 TEXT`, `created_at` | UQ(`track_id`, `content_sha256`)。FKで楽曲の所属一致を保証。payloadはオブジェクト。UPDATE / DELETE禁止 |

- 初回payloadは`tags`をキーとするオブジェクト。タグ名と重みの対応を保持する。曲名・アーティスト名などの未取得項目を補完しない。
- AdapterでPython辞書の文字列表現を安全に解析し、タグ名の非空・重みの整数1〜100を検証する。空辞書は「取得済み・タグなし」として保存する。
- 同じ楽曲・同じ内容は既存のメタデータ本体を再利用し、変更された内容だけを追加する。
- hashが一致した場合もpayloadを比較し、食い違いがあれば拒否する。
- ユーザー属性は未取得のため、初回はユーザーの識別だけを管理する。架空の属性や空の属性レコードを作らない。

## 取り込み実行

| テーブル | カラム | キー・制約 |
| --- | --- | --- |
| `import_runs` | `id`, `dataset_id`, `dataset_version_id UUID`, `adapter_name TEXT`, `adapter_version TEXT`, `pipeline_version TEXT`, `dependency_lock_sha256 TEXT`, `config JSONB`, `input_fingerprint TEXT`, `input_sealed_at TIMESTAMPTZ?`, `status TEXT`, `started_at TIMESTAMPTZ`, `finished_at TIMESTAMPTZ?`, `result_snapshot_id UUID?`, `stats JSONB`, `error_type TEXT?` | 所属する版・結果snapshotの所属と版をFKで保証。`input_fingerprint`は非一意。config・statsはオブジェクト |
| `import_run_sources` | `dataset_id`, `dataset_version_id UUID`, `import_run_id UUID`, `source_file_id UUID` | PK(`import_run_id`, `source_file_id`)。run・source fileの所属と版をFKで保証 |

- 初回は一つのデータセット版に属するsource fileを入力とする。将来の複数配布元の結合は、その出典・ID対応を確認してから拡張する。
- 再実行・リトライは新しいrunを作成する。同じ入力のrunを一意制約でまとめない。
- `config`は実際の件数・抽出方法・seed・正規化条件を明示したオブジェクト。省略された既定値も実行時に展開して記録する。
- `stats`は集計件数・重複・検証結果。認証情報・例外本文・生ユーザー情報を保存しない。
- 設定・入力・出典の参照はrun開始時に固定する。status・終了情報・検証結果だけを終了時に確定する。
- run開始の一つのトランザクションで入力ファイル参照を登録し、`input_sealed_at`を一度だけ設定する。run INSERTの遅延制約トリガーで、COMMIT時の封印・参照と入力fingerprintの一致を検証する。
- 入力参照のINSERT / DELETEと封印はrun行を`FOR UPDATE`でロックする。封印後の追加・削除、常時のUPDATEを禁止する。封印後のrunでは入力・設定・開始情報も変更できない。

| status | 必須・禁止する値 |
| --- | --- |
| `running` | `finished_at`, `result_snapshot_id`, `error_type`はNULL |
| `succeeded` | 終了時刻と公開済み結果snapshotが必須。`error_type`はNULL |
| `failed` | 終了時刻と例外型が必須。結果snapshotはNULL |

- `running → succeeded / failed`の遷移だけを許可する。終了したrunのUPDATE / DELETEは禁止する。

## メタデータスナップショット

| テーブル | カラム | キー・制約 |
| --- | --- | --- |
| `metadata_snapshots` | `id`, `dataset_id`, `dataset_version_id UUID`, `created_by_import_run_id UUID`, `input_fingerprint TEXT`, `content_fingerprint TEXT`, `manifest JSONB`, `created_at`, `published_at TIMESTAMPTZ?` | UQ(`dataset_version_id`, `input_fingerprint`)・UQ(`created_by_import_run_id`)。版・作成runの所属と版をFKで保証。manifestはオブジェクト |
| `snapshot_tracks` | `dataset_id`, `snapshot_id UUID`, `track_id UUID`, `track_metadata_id UUID?` | PK(`snapshot_id`, `track_id`)。snapshotと楽曲の所属一致、メタデータの所属・楽曲一致をFKで保証 |
| `snapshot_users` | `dataset_id`, `snapshot_id UUID`, `user_id UUID` | PK(`snapshot_id`, `user_id`)。snapshotとユーザーの所属一致をFKで保証 |
| `snapshot_artifacts` | `dataset_id`, `snapshot_id UUID`, `role TEXT`, `artifact_id UUID` | PK(`snapshot_id`, `role`)。snapshotと保存物の所属一致をFKで保証 |

- `track_metadata_id = NULL`はメタデータ未取得。`payload.tags = {}`の取得済み・タグなしと区別する。
- 初回の保存物の役割は`tracks`・`users`で、どちらもParquet。役割名とmanifestにより、保存物の形式・行数・内容を照合する。
- `manifest`は入力ファイル・処理版・設定・楽曲/ユーザー構成・メタデータhash・出力ファイルhashと件数を固定する。
- Parquetの識別にはデータセットの`key`と元IDを使用する。ランダムなDB内部UUIDを実験データの移植に必須としない。
- 入力fingerprintは配布版の出典・manifest hash、使用ファイルhash、処理版、依存lock hash、展開済み設定から計算する。
- 内容fingerprintは元IDで並べた構成・メタデータhash・出力保存物のhashから計算する。記録日時・run ID・DB内部UUID・環境の絶対パスはhashに含めない。
- 正規化JSONはキーを`C`照合順序でソートし、UTF-8・余分な空白なし・NaN禁止で符号化する。数値は指数表記と小数末尾の0を除く。`recsys_canonical_json`と`recsys_json_sha256`を共通規則とし、処理版とともに固定する。
- Parquetも元IDの行順・タグ順・writer設定を固定し、run ID・生成時刻など実行ごとに変わる情報をファイルに含めない。同一入力の出力hash一致を次段階で確認する。
- 同じ入力の再実行は既存のsnapshotを結果として参照する。内容fingerprintが食い違う場合は失敗とし、新しいsnapshotで上書きしない。

## 外部キーの具体形

- 以下の参照先カラム集合にUQを追加する。小規模な重複カラムは所属・参照整合性をDBで保証するために保持する。

| 子側の参照 | 親側の参照先 |
| --- | --- |
| `source_files / import_runs / metadata_snapshots (dataset_id, dataset_version_id)` | `dataset_versions (dataset_id, id)` |
| `source_files / snapshot_artifacts (dataset_id, artifact_id)` | `data_artifacts (dataset_id, id)` |
| `track_metadata / snapshot_tracks (dataset_id, track_id)` | `tracks (dataset_id, id)` |
| `snapshot_users (dataset_id, user_id)` | `users (dataset_id, id)` |
| `import_run_sources (dataset_id, dataset_version_id, import_run_id)` | `import_runs (dataset_id, dataset_version_id, id)` |
| `import_run_sources (dataset_id, dataset_version_id, source_file_id)` | `source_files (dataset_id, dataset_version_id, id)` |
| `metadata_snapshots (dataset_id, dataset_version_id, created_by_import_run_id)` | `import_runs (dataset_id, dataset_version_id, id)` |
| `import_runs (dataset_id, dataset_version_id, result_snapshot_id)` | `metadata_snapshots (dataset_id, dataset_version_id, id)` |
| `snapshot_* (dataset_id, snapshot_id)` | `metadata_snapshots (dataset_id, id)` |
| `snapshot_tracks (dataset_id, track_id, track_metadata_id)` | `track_metadata (dataset_id, track_id, id)` |

- NULLを許可する結果snapshot・メタデータ参照はFKの`MATCH SIMPLE`を使用する。楽曲・snapshot本体への必須FKは独立して検証する。
- FKだけでは公開状態・runの結果一致などの行をまたぐ条件を保証できないため、下記の制約トリガーを併用する。[PostgreSQLの制約](https://www.postgresql.org/docs/18/ddl-constraints.html)

## 公開・不変性の保証

| 場面 | 保証する動作 |
| --- | --- |
| 保存物の準備 | ファイルを保存し、読戻し・hash・形式・行数・ID対応を検証。DBだけでファイル検証完了とみなさない |
| 公開処理 | 一つのトランザクションでsnapshot・構成・保存物の参照を登録し、run成功・結果参照・公開時刻を確定 |
| 公開漏れ | snapshot INSERTの遅延制約トリガーで、COMMIT時の公開時刻・作成run成功/結果一致・入力fingerprint一致・manifestと全構成/保存物の一致を検証。未完成ならROLLBACK |
| runの成功 | run終了の遅延制約トリガーでも公開済み結果snapshot・版・入力fingerprint一致を検証。既存snapshotを再利用するrunにも適用 |
| 公開後の変更 | snapshotのUPDATE / DELETE、構成3表へのINSERT / UPDATE / DELETEを拒否。UPDATEで別snapshotへ付け替えることも拒否 |
| 公開との競合 | 構成の追加・削除と公開処理は、対象snapshot行の`FOR UPDATE`ロックを取得して直列化 |
| 失敗 | 公開トランザクションをROLLBACKし、別トランザクションでrun失敗を記録。途中の保存物・識別子が残ってもsnapshotから参照させない |

- 構成行のUPDATEは公開前も禁止し、公開前の組立てはINSERT / DELETEで行う。
- `TRUNCATE`も全13表で拒否し、行トリガーの迂回による構成消失を防ぐ。
- 公開前のNULL時刻は組立てトランザクション内だけの状態。未公開snapshotをCOMMITできないため、実験が参照できるsnapshotは公開済みだけになる。
- snapshotの一度だけの公開時刻設定は許可し、他の記録内容は変更しない。内容hash・manifest・構成を変更する場合は別snapshotを作成する。
- runとsnapshotの相互参照は、runの作成後に公開トランザクションで埋める。COMMIT時に状態と相互参照を検証する。
- 検索・推薦はsnapshotを指定し、その構成を経由してメタデータを読む。識別台帳から最新の情報を暗黙に選ばない。
- 公開時にファイル検証し、実験での読込時もチェックサムを照合する。DBの不変性だけで外部ファイルの不変性を保証したとみなさない。
- トリガー・遅延検証は[CREATE TRIGGER](https://www.postgresql.org/docs/18/sql-createtrigger.html)と[SET CONSTRAINTS](https://www.postgresql.org/docs/18/sql-set-constraints.html)に従って実装する。制約を無効化できるDB管理者操作は通常の取り込み経路に含めない。

## manifestの形式

| 値 | 形式・検証 |
| --- | --- |
| 配布版の`source_manifest.files` | ファイル配列。各要素は`name`・`byte_size`・`checksum`（方式と値、未提供時はNULLまたは省略）。方式は初回`md5` / `sha256`。名前の重複を拒否 |
| 入力document | `recsys_input_document(run_id)`。データセット名・配布版の出典/hash・実際の入力ファイルhash・Adapter/処理版・依存lock hash・設定。`input_fingerprint`はこの正規化hash |
| snapshotの`manifest` | `recsys_snapshot_manifest(snapshot_id)`。`input`、元ID順の`tracks`（元IDとメタデータhash / NULL）と`users`、役割別`artifacts`（hashと行数） |
| `content_fingerprint` | snapshot manifestから`input`を除いた値の正規化hash。公開時にDB内の全構成・保存物と照合 |

- 入力hashはrun INSERT前に確定する。開始トランザクションで参照を追加し、封印時にDBの入力documentと照合する。
- 再取り込み時の生成物と既存snapshotの内容hash比較、実ファイルのchecksum・内容検証は共通取り込み処理で行う。DBで検証済み保存物の宣言だけから実ファイルの正しさを推定しない。

## 後続で追加するもの

| 対象 | 接続先・方針 |
| --- | --- |
| ユーザー属性 | 実配布カラムを確認後に、不変の本体とsnapshot内参照を追加。初期snapshotはIDのみのまま保持 |
| 分割 | データセット版・再生イベントの保存物・抽出条件に紐付け、snapshotとは別に管理 |
| Embedding | snapshotや分割/学習保存物、生成モデル・モデル版・前処理に紐付ける |
| 実験 | データセット版・snapshot・分割・Embedding・モデル設定を参照。データセットの一致と公開状態を検証 |
| 会話・楽曲対応付け | 実配布カラムと必要性を確認した段階で追加 |

## 検証対象

| 入力・操作 | 期待結果 |
| --- | --- |
| 同じ元IDで再取り込み | 同じ内部IDを使用。新しいrunを記録 |
| 同じ内容のタグ行が複数存在 | 一つのメタデータ本体にまとめ、重複件数を記録 |
| 同じ元ID・異なるタグ内容が一つの入力内で競合 | 失敗。黙って先頭/末尾を採用しない |
| 元データの変更によるタグ変更 | 新しい版/snapshotから新しい本体を参照し、過去の参照は維持 |
| 別データセットの楽曲/ユーザー/保存物参照 | FKで拒否 |
| 同じデータセットの別楽曲のメタデータ参照 | FKで拒否 |
| 公開後の本体・構成・保存物参照の変更 | トリガーで拒否 |
| 公開未完了、Parquet検証失敗、同時公開/追加 | 未完成集合をCOMMITしない。公開後の追加を拒否 |
| 同じ入力fingerprintで異なる出力 | 失敗。既存snapshotを維持 |

- 初回サブセットは元ID文字列順の楽曲100件・ユーザー100件。評価実験の分割条件は未確定。
- DBの所属・出典・封印・公開・不変性と、Adapterによる重複行処理・元ファイルからの取り込みを検証する。[実行手順](music4all-import.md)
