"""Popularity全件評価の互換CLI / 共通実装はfull_evaluation。"""

from music_recsys.full_evaluation import main, render, run

__all__ = ["main", "render", "run"]

if __name__ == "__main__":
    main()
