# listening-v2の探索・Notebook統合

ユーザーの「続けて」により、前回の次段階であるv2のNotebook統合を進める。元の検証UX仕様・評価v2仕様を引き継ぎ、新しいモデルや課金APIは追加しない。

## 統合仕様

- `experiment.protocol="listening-v2"`を明示する。既定は既存Binary。
- `[evaluation]`は既存ListeningConfigの設定を共有。主期間・日数重み・Kを開始前に固定する。
- v2の主指標には`selection.task`も必要。discoveryのbinary_ndcg/recall、retention_proxyのgraded_ndcg、repeatのgraded_ndcg/repeat_recallを区別する。未指定は選択待ちで、暗黙の最良決定をしない。
- モデルの実行は共通Runnerへ委譲し、new/repeatの各ランキングを一度だけ生成する。結果を探索・選択・分析・MLflow用の表示形式へ変換する処理をsrcに置く。
- bundleのmanifestと全参照ファイルのhashを条件・固定・再利用で確認する。Testの内容は探索や03へ渡さない。Testでは元TrainとValidationの源ファイルhash、分割・境界、学習履歴の包含を照合し、Train＋Validationで再学習する。
- 01は選択したValidation bundleの観測分布・正解人数を表示する。02はタスク・期間・重みを混ぜず、Binary/Gradedの曲線、ユーザー単位95% CI、選択・最終比較を表示する。03は同じ選択artifactからモデルとタスクを読み、new/repeatの候補を正しく表示する。
- v2 Beyond-Accuracyは現段階では未測定と明示し、v1の定義を暗黙に適用しない。効率・実測使用量は既存v2結果から読む。

## 実装順・検証

1. [x] protocol adapterと設定・bundle指紋。v1回帰、v2のGrid・Graded選択・未指定主指標・条件変更拒否をRED→GREEN。
2. [x] 固定・Test・03引継ぎ。Test源ファイル不一致、学習不足、改変、成功再利用をRED→GREEN。
3. [x] v2分析と01/02/03。タスク別・条件別CI、主指標ラベル、観測分布、repeat候補を検証。fresh kernelで3冊を実行。
4. [x] 再現可能な合成例と説明を追加。全テスト・静的解析・ビルド・CLI全工程を確認し、独立した最終レビュー1回を行う。重要指摘は再現テストで修正し、論理単位でコミット・push。

条件キーの取り違え、評価窓やλの事後選択、binary/graded混在、Testを使う探索、元観測値のモデル漏洩を重点確認する。

## 完了記録（2026-10-15）

- 全テスト：`MPLCONFIGDIR=/private/tmp/music-recsys-mpl-cache .venv/bin/python -m pytest -q` → 286 passed / 3 skipped。隔離DB・大規模生成の任意試験は今回再実行せず、前回の記録を保持する。
- Ruff check / format、`git diff --check`、wheel / sdistビルドが成功。配布物に新規モジュール・合成例・設定・説明を含むことを確認した。
- 01/02/03と最終Test後の02をfresh kernelで実行。合成例のItemCF・PopularityそれぞれでCLI Smoke→Grid→再利用→選択→03確認→固定→Test→再利用を実行し、2モデルの最終比較と4種の図を確認した。
- 実MLflow SQLiteでv2成功・失敗・指標保存・生IDを含まない集計・成功再利用を確認した。
- 独立レビュー1回。実験名を評価設定のループ変数で上書きするImportantを修正し、設定名保持と二モデルの系列分離・K重複なしを検証。CLI発見時の再現テストはRED→GREEN。探索図の条件ラベルも再現テスト後に追加した。未解消のCritical / Importantと保留Minorはない。

## 判断と制約

- 既存v1のAPIと成果物互換性を維持し、v2は明示指定・主タスクと主指標の組を要求する。未指定なら選択しないため、診断探索から最終評価へ直接は進めない。
- v2 Beyond-Accuracyは未測定のまま保持する。定義と測定を追加するまでv2の多様性トレードオフは比較できない。
- 学習データのキャッシュは1 bundleに限定し、再利用前に内容hashを確認する。Pythonへの再展開を避ける代わりに、読込み時のhash検証I/Oは残る。
- CIは主期間・主日数重みだけで算出し、補助条件は平均と人数を保持する。補助条件のCIはこの画面では得られない。
- Testの整合性照合は元ファイル記録・学習総件数・既知観測の包含まで。元イベントを完全再構成しないため、手作業で窓外やcoldイベントを別ペアへ割り当てたbundleまでは検出保証しない。共通exporterを使用する。
- 既存v2 backendの大規模性能・上限・実データ精度は差分レビューの対象外とした。今回の結果だけでは全量モデルの性能やプロセスRSSを保証しない。
- 独立レビューは読取り専用、全テスト・CLI・実MLflowの実行は著者が担当した。実行の独立再検証はなく、保存した検証記録に基づく。
