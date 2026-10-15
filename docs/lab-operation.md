# 研究室サーバーでの運用

## 環境

- Python仮想環境・Parquet・JSONで動作、Docker・sudo不要
- 計算はサーバー、MacからはSSHとブラウザで接続
- 設定は `configs/popularity-plays.json`、保存先は[構成](architecture.md)を参照
- 元データを取得済みなら再ダウンロードせずchecksumを確認して再利用

## 準備と評価

リポジトリのルートで実行する

```bash
uv sync --locked --group data --group models --group notebook --group tracking
.venv/bin/python -u -m music_recsys.lab_data
.venv/bin/python -u -m music_recsys.full_popularity --config configs/popularity-plays.json
```

- `lab_data` は学習期間に観測された全ユーザーを対象とする
- `--users N` は明示した小規模試験だけで使用
- 学習境界は2017-03-29、Validation/Test境界は2018-09-21
- 未来の再生有無でユーザーを選ばない
- `data/benchmarks/music4all/ready.json` がデータ準備完了の目印
- Test入力の生成はTest評価の実行ではない
- 人気順は学習期間の再生回数、同数なら曲ID順
- 全件処理はCPUで実行、DuckDBは原則2スレッド・2GB
- 評価はユーザー単位で処理し、全再生イベントをPythonオブジェクトに展開しない

長時間処理はtmux内で実行する

```bash
tmux new -s popularity
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  .venv/bin/python -u -m music_recsys.full_popularity --config configs/popularity-plays.json
```

- `Ctrl-b d` で切断、`tmux attach -t popularity` で再接続
- 実行ログは `data/experiments/popularity-plays/run.log`
- `result.json` は評価・図の生成が成功した後に保存
- 失敗時はログと実行プロセスを確認し、原因を直して再実行
- 成功済みの結果は上書き拒否、別条件は別のoutputを指定
- 実行中の入力・設定・コードは変更しない

## Notebook

```bash
.venv/bin/jupyter lab --no-browser --ip=127.0.0.1 --port=8888
```

Mac側の接続例

```bash
ssh -L 8888:127.0.0.1:8888 <研究室サーバー>
```

- Jupyterが表示するURLをブラウザで開く、トークンは共有しない
- 各Notebookで `popularity-plays.json` を明示選択して読み込む
- 00は準備状態、01は母集団と分布、02は保存済みの4観点の結果
- 00の `ACTION = "evaluate"` は評価開始、既定のNoneでは実行しない
- 03は小規模共通Runnerで選択済みの設定を確認する機能
- 全件Popularityの個別推薦は `recommendations.parquet` に保存
- Jupyterの一時ファイルは標準のユーザー用保存先を使用

## 保管

- コード・設定をGitで管理、元データ・個別履歴・結果は管理外
- 成功後は実験フォルダと入力manifestを別媒体へコピー
- サーバー内だけのコピーはバックアップと見なさない
- データ保存先を移す場合はlab_dataの各パスとJSONの入力パスを揃える
- 保存物を照合してから旧保存先を削除する
