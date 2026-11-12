"""Candidate diagnostics: independent set oracle, full population and immutable publication."""

import json
import os
import runpy
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from backend.infrastructure.datasets.candidates import CandidateWriter
from backend.infrastructure.datasets.listening_data import file_sha256, validate_bundle
from backend.infrastructure.research.configuration import load_settings
from backend.infrastructure.research.hybrid_config import plan
from backend.infrastructure.research.pool_report import run
from backend.infrastructure.research.pool_strata import recall_values, summarize, training_bands
from backend.infrastructure.research.search import load_study_config
from backend.infrastructure.research.store import atomic_json
from backend.interfaces.notebook.pool_figures import figures


def check_recall(xp):
    a = np.full((3, 200), -1, dtype=np.int32)
    b = a.copy()
    a[0] = np.arange(200)
    b[0] = np.arange(50, 250)
    a[1, :2] = [3, 7]
    b[1, :2] = [7, 11]
    truths = [[0, 50, 149, 199, 249], [3, 7, 11, 299], []]
    bands = np.arange(300) % 4
    for band, valid, values in recall_values([xp.asarray(a), xp.asarray(b)], truths, bands, xp=xp):
        host = np.asarray if xp is np else xp.asnumpy
        for i, truth in enumerate(truths):
            truth = {v for v in truth if band is None or bands[v] == band}
            pools = [
                set(a[i, :100]),
                set(b[i, :100]),
                set(a[i, :100]) | set(b[i, :100]),
                set(a[i]),
                set(b[i]),
            ]
            expected = [len(pool & truth) / len(truth) if truth else 0 for pool in pools]
            np.testing.assert_allclose(host(values)[i], expected)
            assert bool(host(valid)[i]) == bool(truth)


def test_recall_matches_independent_sets_with_overlap_padding_and_band_denominators():
    check_recall(np)


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)
def test_gpu_recall_matches_set_oracle():
    import cupy as cp

    check_recall(cp)


def test_seed_average_paired_differences_empty_bands_and_alignment():
    key = ("new", 90, "history", "Q1")
    empty = ("new", 90, "history", "Q2")
    first = {
        key: (["u1", "u2"], np.array([[0, 0, 1, 1, 0], [1, 0, 1, 1, 0]])),
        empty: ([], np.empty((0, 5))),
    }
    second = {
        key: (["u1", "u2"], np.array([[1, 0, 1, 1, 0], [1, 0, 1, 1, 0]])),
        empty: ([], np.empty((0, 5))),
    }
    rows = summarize([first, second], np.array([0, 0, 0]), group="pair", xp=np)
    row = next(r for r in rows if r["stratum"] == "Q1" and r["scope"] == "union_minus_source_a100")
    assert row["mean"] == 0.25 and row["n_evaluated"] == 2 and row["n_excluded"] == 1
    assert all(r["mean"] is None for r in rows if r["stratum"] == "Q2")
    second[key] = (["u2", "u1"], second[key][1])
    with pytest.raises(ValueError, match="eligible users"):
        summarize([first, second], np.array([0, 0, 0]), group="pair", xp=np)


@pytest.fixture
def saved_hybrid(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    manifest, paths, counts = validate_bundle(bundle / "validation")
    raw = load_settings("configs/hybrid/base.json")
    root = tmp_path / "hybrid"
    root.mkdir()
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "DO_NOT_READ_TEST"),
        features=str(bundle / "features.json"),
        output=str(root),
    )
    for model, source in raw["pipeline"]["sources"].items():
        source["validation"] = str(
            tmp_path / "sources" / (model + ("-{seed}" if model == "bpr" else ""))
        )
        for seed in (42, 43, 44) if model == "bpr" else (0,):
            directory = Path(source["validation"].format(seed=seed))
            directory.mkdir(parents=True)
            atomic_json(directory / "config.json", dict(settings=dict(parameters={})))
            atomic_json(
                directory / "result.json", dict(status="succeeded", phase="validation", model=model)
            )
    path = tmp_path / "hybrid.json"
    atomic_json(path, raw)
    config = load_study_config(path)
    spec, retrieval, sources, conditions = plan(config)
    signature = dict(
        settings=json.loads(json.dumps(asdict(config), default=str)),
        manifest=manifest,
        features_sha256=file_sha256(config.features),
        source_sha256="frozen-source",
    )
    atomic_json(
        root / "config.json",
        dict(
            **signature,
            pipeline=asdict(spec),
            retrieval_m=retrieval,
            sources=sources,
            conditions=[asdict(c) for c in conditions],
        ),
    )
    atomic_json(
        root / "result.json",
        dict(status="succeeded", phase="validation", population=counts, artifacts={}),
    )
    users, items, _, _, _ = training_bands(paths["learning"])
    pq.write_table(
        pa.table(
            dict(
                item_id=[r[0] for r in items],
                users=[r[1] for r in items],
                plays=[r[2] for r in items],
            )
        ),
        root / "catalog.parquet",
    )
    learning = pq.read_table(paths["learning"]).to_pylist()
    for name, source in sources.items():
        directory = root / "candidates" / name
        directory.mkdir(parents=True)
        atomic_json(
            directory / "config.json", dict(signature=signature, source=source, retrieval_m=200)
        )
        writer = CandidateWriter(directory / "candidates.parquet")
        for user, _ in users:
            known = {r["item_id"] for r in learning if r["user_id"] == user}
            for task in ("new", "repeat"):
                indices = [
                    i for i, item in enumerate(items) if (item[0] in known) == (task == "repeat")
                ]
                writer.add(
                    dict(
                        user_id=user,
                        task=task,
                        item_indices=indices,
                        raw_scores=[1.0] * len(indices),
                        source_ranks=list(range(1, len(indices) + 1)),
                        candidate_count=len(indices),
                    )
                )
        writer.close()
        atomic_json(
            directory / "result.json",
            dict(
                status="succeeded",
                phase="validation",
                signature=signature,
                source=source,
                population=counts,
                retrieval_m=200,
                artifacts=dict(
                    candidates=dict(
                        path="candidates.parquet",
                        sha256=file_sha256(directory / "candidates.parquet"),
                    )
                ),
            ),
        )
    return config


def test_report_preserves_inputs_has_full_population_nulls_and_no_test_read(saved_hybrid):
    config = saved_hybrid
    before = {p: file_sha256(p) for p in config.output.rglob("*") if p.is_file()}
    output, rows = run(
        config, xp=np, execution=dict(backend="numpy test oracle"), save_figures=figures
    )
    assert all(file_sha256(p) == digest for p, digest in before.items())
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "succeeded" and result["selection"] == "not_selected"
    assert len(list(output.glob("*.parquet"))) == 4
    assert not (config.output / "pool-strata.partial").exists()
    overall = [r for r in rows if r["stratum_kind"] == "all"]
    assert all(r["n_population"] == 4 for r in overall)
    assert all(r["mean"] == 1 for r in overall if r["metric"] == "candidate_recall")
    assert all(r["mean"] == 0 for r in overall if r["metric"] == "recall_difference")
    assert any(r["mean"] is None for r in rows)
    with pytest.raises(FileExistsError):
        run(config, xp=np, execution={}, save_figures=figures)


@pytest.mark.parametrize("corruption", ["artifact", "catalog", "signature"])
def test_corrupt_evidence_never_publishes(saved_hybrid, corruption):
    config = saved_hybrid
    root = config.output
    if corruption == "artifact":
        with (root / "candidates/ease/candidates.parquet").open("ab") as stream:
            stream.write(b"changed")
    elif corruption == "catalog":
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(
            pa.table(dict(item_id=["wrong"], users=[1], plays=[1.0])), root / "catalog.parquet"
        )
    else:
        p = root / "candidates/ease/result.json"
        value = json.loads(p.read_text())
        value["signature"]["source_sha256"] = "changed"
        atomic_json(p, value)
    with pytest.raises(ValueError):
        run(config, xp=np, execution={}, save_figures=figures)
    assert not (root / "pool-strata").exists()


def test_failed_publication_preserves_partial_and_refuses_retry(saved_hybrid):
    def fail(*args):
        raise RuntimeError("figure failure")

    with pytest.raises(RuntimeError, match="figure failure"):
        run(saved_hybrid, xp=np, execution={}, save_figures=fail)
    assert not (saved_hybrid.output / "pool-strata").exists()
    assert (saved_hybrid.output / "pool-strata.partial/config.json").exists()
    assert not (saved_hybrid.output / "pool-strata.partial/result.json").exists()
    with pytest.raises(FileExistsError):
        run(saved_hybrid, xp=np, execution={}, save_figures=figures)


def test_unmeasured_plots_skipped_but_zero_is_rendered(tmp_path):
    row = dict(
        group="pair",
        task="new",
        window_days=90,
        stratum_kind="history",
        stratum="Q1",
        scope="union_minus_source_a200",
        metric="recall_difference",
        mean=None,
    )
    figures([row], tmp_path)
    assert not list(tmp_path.glob("*.png"))
    figures([dict(row, mean=0)], tmp_path)
    assert len(list(tmp_path.glob("*.png"))) == 1


@pytest.mark.parametrize("change", ["missing_user", "bad_index"])
def test_rehashed_candidates_still_require_full_population_and_valid_indices(saved_hybrid, change):
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = saved_hybrid.output
    for directory in (root / "candidates").iterdir():
        p = directory / "candidates.parquet"
        table = pq.read_table(p)
        rows = table.to_pylist()
        if change == "missing_user":
            rows = [r for r in rows if r["user_id"] != "u4"]
        else:
            rows[0]["item_indices"][0] = 999
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), p)
        result = json.loads((directory / "result.json").read_text())
        result["artifacts"]["candidates"]["sha256"] = file_sha256(p)
        atomic_json(directory / "result.json", result)
    with pytest.raises(ValueError, match="incomplete|catalog index"):
        run(saved_hybrid, xp=np, execution={}, save_figures=figures)
    assert not (root / "pool-strata").exists()


def test_cli_plan_does_not_read_data_or_start_gpu(monkeypatch, capsys):
    import sys

    from backend.interfaces.cli.hybrid_strata import main

    monkeypatch.setattr(sys, "argv", ["hybrid_strata", "--config", "configs/hybrid/base.json"])
    monkeypatch.setattr(
        "backend.infrastructure.research.pool_report.run",
        lambda *a, **kw: pytest.fail("Plan must not run"),
    )
    main()
    assert json.loads(capsys.readouterr().out)["population"] == "all learning users"


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)
def test_gpu_report(saved_hybrid):
    import cupy as cp

    output, rows = run(
        saved_hybrid, xp=cp, execution=dict(backend="cupy test"), save_figures=figures
    )
    assert (output / "result.json").exists()
    assert all(
        r["mean"] == pytest.approx(1)
        for r in rows
        if r["metric"] == "candidate_recall" and r["mean"] is not None
    )
    assert all(
        r["mean"] == pytest.approx(0)
        for r in rows
        if r["metric"] == "recall_difference" and r["mean"] is not None
    )
