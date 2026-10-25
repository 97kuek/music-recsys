"""Music4All原版の曲名・artistを既存モデル用の表示対応表へ取り出す。"""

import argparse
from pathlib import Path

from src.infrastructure.datasets.music4all_metadata import prepare_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="検証済み原版の保存先")
    parser.add_argument("--config", type=Path, required=True, help="既存Music4Allアプリの設定")
    parser.add_argument(
        "--output", type=Path, required=True, help="新規保存先、既存先は上書きしない"
    )
    args = parser.parse_args()
    print(prepare_metadata(args.source, args.config, args.output))


if __name__ == "__main__":
    main()
