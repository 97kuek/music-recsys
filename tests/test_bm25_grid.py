import json
import os
import runpy
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


def test_shared_grid_requires_gpu_bm25_before_creating_outputs(tmp_path):
    from src.infrastructure.research.configuration import load_settings
    from src.interfaces.cli.bm25_grid import run

    raw = load_settings("configs/bm25-grid.json")
    raw["model"]["parameters"]["use_gpu"] = False
    raw["experiment"]["output"] = str(tmp_path / "never-created")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="explicit GPU BM25"):
        run(path, 1.2, 0.75)
    assert not (tmp_path / "never-created").exists()


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)
def test_shared_prefixes_match_independent_bm25_cpu_rankings_and_scores(tmp_path):
    import duckdb
    import pyarrow.parquet as pq

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import validate_bundle
    from src.infrastructure.evaluation.full_scoring import score_cpu
    from src.infrastructure.recommenders.full_bm25 import FullBM25
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.parameters import conditions, verify
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.bm25_grid import run

    bundle = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"](tmp_path / "bundle")
    raw = load_settings("configs/bm25-grid.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "never-test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "study"),
    )
    raw["search"].update(
        conditions=[dict(neighbors=n, k1=1.2, b=0.5) for n in [2, 3]], max_trials=2
    )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(raw))
    outputs = run(path, 1.2, 0.5)
    config = load_study_config(path)
    manifest, paths, counts = validate_bundle(config.validation_dataset)
    features = load_features(
        config.features,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    with duckdb.connect() as connection:
        popularity_rows = connection.execute(
            "SELECT item_id,sum(play_count),count(*) FROM read_parquet(?) GROUP BY item_id ORDER BY sum(play_count) DESC,item_id",
            [str(paths["learning"])],
        ).fetchall()
    popularity = [r[0] for r in popularity_rows]
    for _, trial in conditions(config):
        actual, _ = verify(trial)
        independent = FullBM25(
            paths["learning"], neighbors=trial.parameters["neighbors"], k1=1.2, b=0.5
        )
        for row in pq.read_table(trial.output / "recommendations.parquet").to_pylist():
            history = set(
                independent.items[independent.histories[independent.users[row["user_id"]]].indices]
            )
            assert row["item_ids"] == independent.rank(row["user_id"], history, row["task"])
        cpu = tmp_path / f"cpu{independent.neighbors}"
        cpu.mkdir()
        accuracy, diversity, _ = score_cpu(
            replace(trial, output=cpu),
            paths,
            counts,
            features,
            {r[0]: r[2] for r in popularity_rows},
            popularity,
            {item: i for i, item in enumerate(popularity)},
            independent,
            None,
            lambda _: None,
        )
        for expected_rows, observed_rows in [
            (accuracy, actual["accuracy"]),
            (diversity, actual["diversity"]),
        ]:
            for left, right in zip(expected_rows, observed_rows, strict=True):
                assert left.keys() == right.keys()
                for key, value in left.items():
                    if isinstance(value, float):
                        np.testing.assert_allclose(value, right[key], atol=1e-10)
                    else:
                        assert value == right[key]
        assert actual["efficiency"]["model"] == "bm25"
    assert len(outputs) == 2
    assert not (tmp_path / "never-test").exists()
    with pytest.raises(FileExistsError):
        run(path, 1.2, 0.5)
