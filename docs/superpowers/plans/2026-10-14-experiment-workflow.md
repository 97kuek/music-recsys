# Experiment workflow implementation plan

Spec: [検証フロー](../../experiment-workflow.md)と最新ユーザー依頼。
Execution: 現在の作業ブランチで順に実装。進捗は`/private/tmp/music-recsys-workflow/progress.md`に記録し、既存の調査ファイルを保持する。

## Task 1: 設定・探索・状態・選択・最終Test

- [x] `tests/test_experiment_search.py`に、実際に順位が変わる合成factoryとフェーズ付きJSONを使うテストを書く。Smoke必須、1D/2D grid、seed平均、未指定主指標、予算切れ、失敗／再開、成功再利用、条件変更拒否、03確認、固定、Test学習出典、改変検出を確認する。
- [x] REDを確認してから`experiment_search.py`を実装する。TOMLを唯一の設定として読み、atomic JSONと排他ロック、trial key、study fingerprintを持つ。`create_recommender`をparameter/seed対応にする。`run_benchmark`と`save_report`を再利用し、全Kは共通関数から計算する。
- [x] `selection.json`からValidationだけを読む引継ぎを実装する。固定時にreview・Test hashを記録し、最終Testは選択の変更や探索を行わない。GREENを確認する。

## Task 2: 分析・可視化・次の操作

- [x] `tests/test_search_analysis.py`で1D線／2Dヒートマップ、主指標なし、失敗／未完了、境界上の最良、差が小さい、条件不一致、未測定値をテストする。
- [x] `search_analysis.py`と必要な描画helperを実装する。生ランキングから共通EvaluatorでK=1..20を得て、計算結果を保存する。CIと履歴層別は既存のユーザー単位分析関数を再利用する。
- [x] Coverage・Novelty、任意のILDと不在理由、Latency P50/P95を実測・計算する。単位と定義を保存し、条件不一致の比較を止める。GREENを確認する。

## Task 3: Notebookの導線と引継ぎ

- [x] Notebook名と経路の期待を新しい役割へ更新し、選択artifactから03へ設定が渡るテストを加えてREDを確認する。
- [x] 02を`02_analysis.ipynb`、03を`03_recommendation.ipynb`に整理する。旧分析の保存結果・CI・履歴分析・効率機能を02に残し、探索／選択／Test比較の7章へ再配置する。03の音源と履歴確認を維持し、モデル設定は選択artifactから取得する。
- [x] 01の既存機能を保持し、保存済みGroundTruth分布を参考資料として追加する。Setupは各Notebookで1か所とし、共通TOML参照と次の操作・戻り先を表示する。
- [x] 新Notebookをfresh kernelで実行し、探索未実行・成功・失敗・選択済み・条件不一致を確認する。

## Task 4: CLI・説明・最終検証

- [x] `music-recsys-search`のCLIと再現可能な合成用TOML／フェーズ付き入力を追加する。README・CONTRIBUTING・Notebook間リンク・既存手順を新名へ更新する。
- [x] 通常テスト、Notebook fresh kernel、MLflow SQLite、静的解析・整形・diff check・ビルドを実行する。実験結果の代わりにテスト用モデルを使い、未実装モデルの値を出さない。
- [x] `executing-plans`に従い最終差分を独立レビューする。重要な指摘は再現テストをRED→GREENで修正し、全テストを再実行する。

Constraints: Binary主条件、K=1..20、主K=10、Gradedは未確定と表示、Test探索禁止、依存・DB・複数モデルの新規実装は追加しない。コミット・pushなし。
