# 開発

## 環境

- Python 3.12、uv、Linux / macOS
- 実装は `src/music_recsys/`、テストは `tests/`
- 標準運用にDB・Dockerは不要

```bash
uv sync --locked --group data --group models --group notebook --group tracking
.venv/bin/pytest
.venv/bin/ruff check src tests notebooks migrations
.venv/bin/ruff format --check src tests notebooks migrations
uv build
```

| 確認 | コマンド |
| --- | --- |
| Notebook以外 | `.venv/bin/pytest -m 'not notebook'` |
| Notebook | `.venv/bin/pytest -m notebook` |
| DB | `MUSIC_RECSYS_TEST_DB=1 uv run --no-sync --env-file .env pytest tests/db` |
| 大規模生成 | `MUSIC_RECSYS_TEST_SCALE=1 .venv/bin/pytest tests/test_temporal_scale.py tests/test_listening_scale.py` |

DB試験は専用schemaを使うため、許可されたテスト用DBでのみ実行する
大規模試験はサーバーの実行ルールと空き容量を確認してから実行する

## 変更管理

- `main`から作業ブランチを作成
- 接頭辞は `feat/`・`fix/`・`docs/`・`chore/`
- コミット・pushは明示的な依頼がある場合のみ
- コミットは `feat: ...`・`fix: ...`・`docs: ...` などの種別と日本語の説明
- 文書は各200行以内、作業履歴より現行仕様を優先
- Notebookの書式・データ保護は[AGENTS.md](AGENTS.md)に従う

## 管理対象

| Gitに含める | Gitに含めない |
| --- | --- |
| コード・テスト・設定・Notebook・文書 | 仮想環境・キャッシュ・ビルド成果物 |
| 小さな合成fixture・参考集計 | 生データ・加工データ・学習済みモデル |
| lock・出典・再現手順 | `.env`・認証情報・生ログ・個別推薦 |

研究室での実行は[運用](docs/lab-operation.md)、評価条件は[評価仕様](docs/listening-evaluation.md)を参照
