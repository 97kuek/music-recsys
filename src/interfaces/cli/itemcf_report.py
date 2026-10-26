"""保存済みItemCF Gridの比較レポートを作成 / 評価・選択・Testは実行しない。"""

import argparse
from pathlib import Path


def main():
    from src.bootstrap import create_itemcf_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    print(create_itemcf_report().write(parser.parse_args().config))


if __name__ == "__main__":
    main()
