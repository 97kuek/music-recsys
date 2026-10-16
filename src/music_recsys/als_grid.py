"""Backward-compatible CLI and imports for the shared factor grid runner."""

from music_recsys.factor_grid import build_report, main, run_grid, variants

__all__ = ["build_report", "main", "run_grid", "variants"]

if __name__ == "__main__":
    main()
