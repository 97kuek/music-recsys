"""Aggregate saved observation bins without loading per-pair rows into notebooks."""


def observation_distribution(config, *, phase="validation"):
    """ペアの生行をNotebookへ展開せず、主評価窓の観測ヒストグラムを集計する。"""
    import duckdb
    import pandas as pd

    from src.infrastructure.datasets.listening_data import read_bundle

    if phase not in {"validation", "test"}:
        raise ValueError("phase must be validation or test")
    manifest, paths = read_bundle(
        config.test_dataset if phase == "test" else config.validation_dataset
    )
    if manifest["phase"] != phase:
        raise ValueError("Observation phase differs from requested phase")
    rows = []
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
        connection.read_parquet(str(paths["observations"])).create_view("observations")
        for name in ("evaluation_play_count", "evaluation_active_days"):
            result = connection.execute(
                f"""SELECT CASE WHEN learning_play_count>0 THEN 'repeat' ELSE 'new' END task,
                CASE WHEN {name}=1 THEN '1' WHEN {name}=2 THEN '2' WHEN {name}<5 THEN '3–4' WHEN {name}<10 THEN '5–9' WHEN {name}<20 THEN '10–19' ELSE '20+' END bin,
                count(*) pairs FROM observations WHERE window_days=? GROUP BY task,bin""",
                [config.evaluation.primary_window_days],
            ).fetchall()
            counts = {(task, bin): count for task, bin, count in result}
            rows.extend(
                {
                    "phase": phase,
                    "window_days": config.evaluation.primary_window_days,
                    "task": task,
                    "observation": name,
                    "bin": bin,
                    "pairs": counts.get((task, bin), 0),
                }
                for task in sorted({task for task, _, _ in result})
                for bin in ("1", "2", "3–4", "5–9", "10–19", "20+")
            )
    return pd.DataFrame(rows)
