import json
from dataclasses import replace

import pytest
from listening_workflow_support import update_manifest_file


def prepared(factory):
    from music_recsys.experiment_search import run_search, select_best, smoke

    config = factory()
    assert smoke(config)["status"] == "succeeded"
    assert run_search(config)["status"] == "succeeded"
    return config, select_best(config)


def test_v2_grid_uses_graded_objective_and_validation_only(listening_study):
    from music_recsys.experiment_search import (
        context,
        inspect_selected,
        run_search,
        select_best,
        smoke,
        trial_rows,
    )

    config = listening_study()
    assert config.name == "listening-grid"
    config = replace(config, test_dataset=config.path.parent / "unavailable-test")
    data, conditions = context(config)
    assert conditions["protocol"] == "listening-v2"
    assert data.provenance["phase"] == "validation"
    assert smoke(config)["status"] == "succeeded"
    assert run_search(config)["planned_trials"] == 4
    assert run_search(config)["reused_trials"] == 4
    rows = trial_rows(config)
    assert all(row["graded_ndcg"] is not None for row in rows)
    selected = select_best(config)
    assert selected["objective"]["task"] == "repeat"
    assert selected["objective"]["metric"] == "graded_ndcg"
    inspection = inspect_selected(config)
    assert {r.task for r in inspection["dataset"].requests} == {"repeat"}
    assert inspection["parameters"] == selected["parameters"]


def test_v2_requires_task_for_objective_and_never_selects_unset_metric(listening_study):
    from music_recsys.experiment_search import run_search, select_best, smoke

    with pytest.raises(ValueError, match="task"):
        listening_study(task=None)
    with pytest.raises(ValueError, match="task|metric"):
        listening_study(task="discovery", metric="graded_ndcg")
    config = listening_study(metric=None)
    smoke(config)
    run_search(config)
    with pytest.raises(ValueError, match="主指標"):
        select_best(config)


def test_changing_gain_condition_is_a_different_experiment(listening_study):
    from music_recsys.experiment_search import load_study, smoke

    smoke(listening_study())
    with pytest.raises(ValueError, match="条件"):
        load_study(listening_study(day_weight=2.0))


def test_validation_views_reuse_learning_data_but_reject_changed_artifacts(listening_study):
    from music_recsys.experiment_search import context

    config = listening_study()
    first, _ = context(config)
    second, _ = context(config)
    assert second.train_interactions is first.train_interactions
    with (config.validation_dataset / "observations.parquet").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        context(config)


def test_v2_selection_review_lock_test_and_reuse(listening_study):
    from music_recsys.experiment_search import freeze_selection, review_selection, run_final_test

    config, _ = prepared(listening_study)
    with pytest.raises(ValueError, match="03"):
        freeze_selection(config)
    review_selection(config, accepted=True)
    freeze_selection(config)
    result = run_final_test(config)
    assert result["status"] == "succeeded"
    assert run_final_test(config)["reused"]


def test_v2_test_source_mismatch_and_incomplete_training_are_rejected(listening_study):
    from music_recsys.experiment_search import freeze_selection, review_selection, run_final_test

    config, _ = prepared(listening_study)
    manifest = config.test_dataset / "manifest.json"
    raw = json.loads(manifest.read_text())
    raw["source_files"]["validation"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(raw))
    review_selection(config, accepted=True)
    freeze_selection(config)
    with pytest.raises(ValueError, match="出典|source"):
        run_final_test(config)


def test_v2_modified_observations_cannot_reuse_final_test(listening_study):
    from music_recsys.experiment_search import freeze_selection, review_selection, run_final_test

    config, _ = prepared(listening_study)
    review_selection(config, accepted=True)
    freeze_selection(config)
    assert run_final_test(config)["status"] == "succeeded"
    with (config.test_dataset / "observations.parquet").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum|条件"):
        run_final_test(config)


def test_v2_test_must_include_validation_learning_facts(listening_study):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from music_recsys.experiment_search import freeze_selection, review_selection, run_final_test

    config, _ = prepared(listening_study)
    path = config.test_dataset / "learning.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    for row in rows:
        if row["user_id"] == "u" and row["item_id"] == "a":
            row.update(play_count=1, active_days=1)
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    update_manifest_file(config.test_dataset, "learning")
    review_selection(config, accepted=True)
    freeze_selection(config)
    with pytest.raises(ValueError, match="Train|Validation|learning"):
        run_final_test(config)
