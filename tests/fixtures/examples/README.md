# 合成fixture

| 場所 | 用途 |
| --- | --- |
| toy/dataset.json | binary-v1の小規模な手計算可能入力 |
| toy/create_listening.py | listening-v2の合成イベント・特徴生成 |
| reports/toy-popularity.json | binary-v1のReader試験用結果 |
| その他のJSON | データ読込み・分析APIの自動テスト用fixture |

- 架空のユーザー・曲・再生履歴
- 合成例の精度・速度を研究上の実データ性能として扱わない
- 既存出力を上書きせず、新しい出力先を指定して再生成する

```bash
.venv/bin/python tests/fixtures/examples/toy/create_listening.py --output data/benchmarks/toy-listening
.venv/bin/python -m src.interfaces.cli.experiment_search --config tests/fixtures/configs/workflow-suite-popularity.toml smoke
```

以後は[検証手順](../../../docs/experiment-workflow.md)に従う
