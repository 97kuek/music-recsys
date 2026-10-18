"""Enrollment, deletion and restart are tested against isolated SQLite only."""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from test_online_analysis import NOW, plan

from src.infrastructure.persistence.sessions import LocalStore


@pytest.mark.parametrize("design", ["exploratory_standard", "exploratory_strategy"])
def test_balanced_reproducible_five_slots_and_immutable_returning_assignments(tmp_path, design):
    study = plan(design)
    assert study.schedule() == plan(design).schedule()
    if design.endswith("standard"):
        assert len({tuple(o) for o in study.schedule()}) == 5
        for position in range(3):
            assert sorted(Counter(o[position] for o in study.schedule()).values()) == [1, 2, 2]
    else:
        assert sorted(Counter(tuple(o) for o in study.schedule()).values()) == [2, 3]
    store = LocalStore(tmp_path / "db.sqlite")
    for i in range(6):
        store.add_participant(f"p{i}", f"code{i}", NOW + timedelta(days=1))
    for i in range(5):
        assignment = store.assign(study, f"p{i}", {"config": "same"}, NOW)
        assert assignment["slot"] == i and assignment["order"] == study.schedule()[i]
    with pytest.raises(ValueError, match="5人"):
        store.assign(study, "p5", {"config": "same"}, NOW)
    restarted = LocalStore(store.path)
    assert restarted.assign(study, "p2", {"config": "same"}, NOW)["slot"] == 2
    with pytest.raises(ValueError, match="別の実験ID"):
        restarted.assign(replace(study, seed=3), "p2", {"config": "same"}, NOW)
    with pytest.raises(ValueError, match="別の実験ID"):
        restarted.assign(study, "p2", {"config": "changed"}, NOW)
    frozen = store.freeze_assignment(study, "p2", {"profile": {"seeds": ["initial"]}}, NOW)
    assert store.freeze_assignment(study, "p2", {"profile": {"seeds": ["leaked"]}}, NOW) == frozen
    store.delete_participant("p0")
    assert store.assign(study, "p5", {"config": "same"}, NOW)["slot"] == 0
    assert store.assign(study, "p2", {"config": "same"}, NOW) == frozen
    store.purge(NOW + timedelta(days=1))
    assert store.analysis_snapshot(NOW + timedelta(days=1)) == ([], [])
    with pytest.raises(PermissionError):
        store.freeze_assignment(study, "p2", {}, NOW + timedelta(days=1))


def test_atomic_concurrent_enrollment_and_same_participant(tmp_path):
    store = LocalStore(tmp_path / "db.sqlite")
    study = plan()
    for i in range(5):
        store.add_participant(f"p{i}", f"c{i}", NOW + timedelta(days=1))

    def enroll(i):
        return store.assign(study, f"p{i}", {}, NOW)

    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(enroll, [0, 0, 1, 2, 3]))
    assert results[0] == results[1]
    assert len({a["slot"] for a in results}) == 4
    assert enroll(4)["slot"] == 4


def test_exploratory_constraints_and_anonymous_enrollment(tmp_path):
    study = plan()
    with pytest.raises(ValueError, match="three/two"):
        replace(study, top_k=10)
    with pytest.raises(ValueError, match="persisted"):
        study.order("p")
    with pytest.raises(PermissionError):
        LocalStore(tmp_path / "db.sqlite").assign(study, "unregistered", {}, NOW)


@pytest.mark.parametrize("design", ["exploratory_standard", "exploratory_strategy"])
def test_cli_preview_and_explicit_existing_participant_enrollment(
    tmp_path, monkeypatch, capsys, design
):
    from datetime import UTC, datetime

    from src.interfaces.cli.online_assignment import main

    study = plan(design)
    source = tmp_path / "study.json"
    source.write_text(json.dumps(study.model_dump()))
    monkeypatch.setattr("sys.argv", ["online_assignment", "--study", str(source)])
    main()
    assert json.loads(capsys.readouterr().out)["schedule"] == study.schedule()
    config = tmp_path / "config.json"
    config.write_text("{}")
    store = LocalStore(tmp_path / "db.sqlite")
    store.add_participant("p", "secret-code-hash", datetime.now(UTC) + timedelta(days=1))
    monkeypatch.setattr(
        "sys.argv",
        [
            "online_assignment",
            "--study",
            str(source),
            "--database",
            str(store.path),
            "--config",
            str(config),
            "--participant-id",
            "p",
        ],
    )
    main()
    output = capsys.readouterr().out
    assert json.loads(output)["order"] == study.schedule()[0]
    assert "input" not in json.loads(output) and "secret-code-hash" not in output
