# 研究室サーバーでの運用

## 構成

- プロジェクト内のPython仮想環境・Parquet・JSON・SQLiteで完結する。Docker・PostgreSQL・sudoは不要。
- MacはSSH接続とブラウザ表示に使い、計算はサーバーで行う。
- 元データは `data/raw/`、全量Parquetは `data/processed/`、100ユーザーの入力は `data/benchmarks/lab-100/`。
- 100ユーザーは動作確認・予備分析用。本格評価の人数・抽出方法・主タスク・主指標は未決定。
- データ保存場所・容量制限・長時間処理のルール・アカウント利用期限・バックアップの有無は研究室に確認する。

## 初回準備と再開

リポジトリのルートで実行する。

```bash
uv sync --locked --group data --group models --group notebook --group tracking
mkdir -p data/lab-setup
tmux new-session -d -s music-recsys-prepare \
  'OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -u -m music_recsys.lab_data >> data/lab-setup/prepare.log 2>&1'
tail -n 20 data/lab-setup/prepare.log
```

- 取得途中の `.part` を再利用し、配布サイズ・MD5一致後に確定名へ変更する。
- 全量イベントをバッチ変換し、元ファイルとParquetのSHA-256をmanifestへ保存する。
- Train境界 `2017-03-29T00:00:00` より前に履歴があるユーザーを元ID順に100人選ぶ。未来の活動量で選ばない。Validation/Test境界は `2018-09-21T00:00:00`。
- コホートの全期間履歴、分割、listening-v2のValidation/Test入力、タグ特徴を保存する。Test入力の生成は評価の実行ではない。
- 全量変換時のDuckDB設定は2スレッド・2GB。プロセス全体のメモリ上限ではない。変換開始前に空き15GiBを確認するが、必要容量の保証ではないため `df -h .` も確認する。
- 失敗したらログ末尾を確認し、原因を解消して同じコマンドを再実行する。既存の完了データは全hashを照合して再利用する。入力や処理版が変わった場合は別の `--output` を指定する。
- `data/benchmarks/lab-100/ready.json` が完了の目印。これがない間は未完了。準備処理はモデル評価を実行しない。
- `tmux has-session -t music-recsys-prepare` で稼働を確認する。同名セッションがある場合は重複起動しない。

## Notebookへの接続

サーバー側で実行する。常駐Notebookにも `tmux` を利用できる。

```bash
bash scripts/lab-notebook.sh
```

Macのターミナルから、普段使っているSSH接続先を指定する。

```bash
ssh -N -L 8888:127.0.0.1:8888 <普段のSSH接続先>
```

サーバーのJupyterが表示するトークン付きURLをMacのブラウザで開く。トークンは共有しない。8888が使用中なら `bash scripts/lab-notebook.sh --port=8889` とし、SSH転送先も8889に揃える。

1. `00_lab_start.ipynb`：取得・整備状況と実データの出典を確認。準備完了後に、手動でSmokeまたはValidation診断を開始する。
2. `01_data.ipynb`：共有設定のValidation入力だけを読み、hash・観測分布・評価可能性を確認する。未準備なら案内して停止する。
3. `02_analysis.ipynb`：保存済み診断結果・K別・ユーザー別の結果を見る。未実行の状態は未実行として表示する。
4. `03_recommendation.ipynb`：設定選択後の推薦確認に使用する。主指標未指定の段階では選択できず、03は未選択として停止する。

各Notebookでimportと設定選択セルを実行し、選択欄からTOMLを選んで「読み込む」を押す。初期状態は未選択で、既定ファイル・環境変数からの自動選択は行わない。別のNotebookでも同じTOMLを選ぶ。

- 初回の予備分析には `workflow-lab-popularity.toml` を選ぶ
- 「読込み済み」を確認して、次の設定確認セルから順に実行する
- ファイル追加後は「一覧を更新」、設定を編集した場合は再読込みする
- 設定を変更したら後続セルを順に再実行し、古い表示を新しい実験の結果と解釈しない

00の評価開始セルは既定 `ACTION = None`。明示した `smoke` / `search` だけを別プロセスで実行する。NotebookやSSHが切れてもログ・成果物はサーバーに残る。各実行は共通Runnerのロック・成功試行再利用を使用する。通常はSmoke確認後にValidationへ進む。

`configs/workflow-lab-popularity.toml` は主タスク・主指標未指定の予備分析用。期間30/90/180日、日数重み0/0.5/1/2を診断し、既定表示は90日・重み1・K=10。これを本格評価の最適条件として扱わない。最良選択・固定・Test評価へ進む際は目的から条件を事前に決め、別の実験出力先を使う。

## 保管と拡張

- コード・設定・Notebook・共有用集計はGit管理。生履歴・加工データ・個別推薦・認証情報はGitへ追加しない。
- ダウンロード可能な元データと、再取得できない実験結果を分けて保管する。結果・設定は実行終了後にMacなどへコピーする。サーバー上だけのコピーはバックアップと見なさない。
- 結果移行時は `data/experiments/lab-100/` のSQLiteと成果物を一緒に、書込み処理の終了後にコピーする。入力の `ready.json` とmanifestも残す。
- 複数プロセスによる多数の同時書込みや共同利用が必要になった時点で、研究室の許可を得てMLflowサーバーやPostgreSQLを検討する。
- PopularityはCPUで動く。GPUを使うモデルを導入するときは研究室のGPU割当て・実行時間のルールを先に確認する。
- 将来データ保存先を移す場合は `lab_data --raw /path/... --cache /path/... --output /path/...` とTOMLの入力パスを揃える。保存物を照合してから旧保存先を整理する。
