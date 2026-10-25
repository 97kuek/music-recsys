import json
import os
import runpy
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.infrastructure.research.complementarity import (
    Complementarity,
    FeaturePrefixes,
    load_sources,
    paired_score_differences,
    recommendation_batches,
    tie_safe_quartiles,
)


def metric(
    rows,
    name,
    left="a",
    right="b",
    k=2,
    window=90,
    stratum_kind="all",
    stratum="all",
):
    return next(
        r
        for r in rows
        if r["metric"] == name
        and r["model_left"] == left
        and r["model_right"] == right
        and r["k"] == k
        and r["window_days"] == window
        and r.get("stratum_kind", "all") == stratum_kind
        and r.get("stratum", "all") == stratum
    )


def test_quartiles_keep_ties_together_and_empty_bands_explicit():
    values = [1, 1, 2, 4, 4, 9, 9, 9]
    labels, metadata = tie_safe_quartiles(values)
    assert metadata["boundaries"] == [1, 4, 9]
    assert all(
        len({labels[i] for i, value in enumerate(values) if value == n}) == 1 for n in set(values)
    )
    assert metadata["groups"][-1] == dict(stratum="Q4", count=0)
    labels, metadata = tie_safe_quartiles([7, 7, 7, 7])
    assert labels.tolist() == [0, 0, 0, 0]
    assert [row["count"] for row in metadata["groups"]] == [4, 0, 0, 0]


def test_popularity_truth_denominators_history_population_and_empty_strata():
    features = {str(i): {"rock": 1} for i in range(4)}
    audit = Complementarity(
        ["a", "b"], features, list("0123"), ks=(2, 3), popularity_strata=[0, 0, 1, 1]
    )
    audit.add("new", fixture_rankings(), {90: [[0, 2, 3], [], []]}, history_strata=[0, 0, 1])
    rows = audit.finish()
    low = metric(rows, "union_recall", stratum_kind="popularity", stratum="Q1")
    high = metric(rows, "union_recall", stratum_kind="popularity", stratum="Q2")
    assert low["mean"] == 1
    assert high["mean"] == 0.5
    assert low["n_evaluated"] == high["n_evaluated"] == 1
    assert low["n_excluded"] == high["n_excluded"] == 2
    measured_zero = metric(
        rows, "recall", left="b", right=None, stratum_kind="popularity", stratum="Q1"
    )
    assert measured_zero["mean"] == 0 and measured_zero["n_evaluated"] == 1
    assert metric(rows, "unique_hits_left", stratum_kind="popularity", stratum="Q1")["mean"] == 1
    assert metric(rows, "unique_hits_right", stratum_kind="popularity", stratum="Q2")["mean"] == 1
    history = metric(rows, "union_recall", stratum_kind="history", stratum="Q1")
    assert history["n_evaluated"] == 1 and history["n_excluded"] == 1
    assert metric(rows, "union_recall", stratum_kind="history", stratum="Q2")["n_excluded"] == 1
    for kind in ("history", "popularity"):
        empty = metric(rows, "union_recall", stratum_kind=kind, stratum="Q4")
        assert empty["mean"] is None and empty["n_evaluated"] == 0
        assert empty["n_excluded"] == (0 if kind == "history" else 3)
    assert metric(rows, "union_recall")["mean"] == pytest.approx(2 / 3)


def fixture_rankings():
    rankings = np.full((3, 2, 20), -1, dtype=np.int32)
    rankings[0, 0, :3] = [0, 1, 2]
    rankings[0, 1, :3] = [1, 2, 3]
    rankings[1, 0, :2] = [0, 1]
    rankings[1, 1, :2] = [0, 2]
    return rankings


def test_union_unique_hits_macro_denominator_and_common_ild():
    features = {"0": {"rock": 1}, "1": {"jazz": 1}, "2": {"rock": 1}}
    audit = Complementarity(["a", "b"], features, list("0123"), ks=(2, 3))
    details = audit.add("new", fixture_rankings(), {90: [[0, 2, 3], [], []], 30: [[], [], []]})
    rows = audit.finish()
    assert metric(rows, "union_recall")["mean"] == pytest.approx(2 / 3)
    assert metric(rows, "union_recall", k=3)["mean"] == 1
    assert metric(rows, "union_recall")["n_evaluated"] == 1
    assert metric(rows, "union_recall")["n_excluded"] == 2
    assert metric(rows, "unique_hits_left")["mean"] == 1
    assert metric(rows, "unique_hits_right")["mean"] == 1
    assert metric(rows, "shared_hits")["mean"] == 0
    assert metric(rows, "recall_gain_over_best")["mean"] == pytest.approx(1 / 3)
    assert metric(rows, "union_recall", window=30)["mean"] is None
    assert metric(rows, "common_ild", right=None, window=None)["mean"] == 1
    assert metric(rows, "common_ild", right=None, window=None)["n_evaluated"] == 2
    assert metric(rows, "common_ild", right=None, k=3, window=None)["n_evaluated"] == 1
    assert metric(rows, "common_ild", right=None, k=3, window=None)["mean"] == 1
    assert metric(rows, "overlap_items", window=None)["mean"] == pytest.approx(2 / 3)
    assert metric(rows, "jaccard", window=None)["mean"] == pytest.approx(1 / 3)
    counts, values = details[90]
    assert counts.tolist() == [3, 0, 0]
    np.testing.assert_allclose(values[0], [2 / 3, 1])
    assert np.isnan(values[1:]).all()


def test_feature_prefixes_match_pairwise_cosine_with_missing_zero_and_short_lists():
    features = {"0": {"rock": 2, "jazz": 1}, "1": {"jazz": 3}, "2": {}}
    rankings = np.full((1, 2, 20), -1, dtype=np.int32)
    rankings[0, 0, :2] = [0, 1]
    rankings[0, 1, :3] = [0, 1, 2]
    values, valid = FeaturePrefixes(features, list("0123")).calculate(rankings)
    assert values[0, 0, 1] == pytest.approx(1 - 1 / np.sqrt(5))
    assert valid[0, 0, 1:].all()
    assert valid[0, 1, 1]
    assert not valid[0, 1, 2:].any()


def test_feature_prefixes_keep_large_tag_space_sparse(monkeypatch):
    from scipy.sparse import csr_matrix

    features = {"0": {f"tag{i}": 1 for i in range(2000)}, "1": {"tag1": 1}}
    prefix = FeaturePrefixes(features, ["0", "1"])
    monkeypatch.setattr(
        csr_matrix, "toarray", lambda *args, **kwargs: pytest.fail("dense tag space")
    )
    rankings = np.full((1, 1, 20), -1, dtype=np.int32)
    rankings[0, 0, :2] = [0, 1]
    values, valid = prefix.calculate(rankings)
    assert valid[0, 0, 1]
    assert values[0, 0, 1] == pytest.approx(1 - 1 / np.sqrt(2000))


def score_source(directory, scores, users=("u", "v")):
    rows = []
    for task, name, weight in (
        ("discovery", "binary_ndcg", -1.0),
        ("discovery", "recall", -1.0),
        ("repeat", "graded_ndcg", 1.0),
        ("repeat", "repeat_recall", -1.0),
    ):
        for user, values in zip(users, scores, strict=True):
            rows.append(
                dict(
                    user_id=user,
                    task=task,
                    metric=name,
                    day_weight=weight,
                    window_days=90,
                    values=values,
                )
            )
    pq.write_table(pa.Table.from_pylist(rows), directory)
    return dict(scores=directory, population=dict(users=3))


def test_paired_user_bootstrap_constant_difference_and_misalignment(tmp_path):
    left = score_source(tmp_path / "a.parquet", [[0.0, 0.1], [0.2, 0.4]])
    right = score_source(tmp_path / "b.parquet", [[0.2, 0.3], [0.4, 0.6]])
    rows = paired_score_differences({"a": left, "b": right}, [2, 20], resamples=40)
    row = metric(rows, "paired_difference_recall", k=20)
    assert row["mean"] == pytest.approx(0.2)
    assert row["ci_low"] == pytest.approx(0.2)
    assert row["ci_high"] == pytest.approx(0.2)
    assert row["n_evaluated"] == 2
    assert row["n_excluded"] == 1
    assert metric(rows, "paired_difference_recall", k=2)["ci_low"] is None
    bad = score_source(tmp_path / "bad.parquet", [[0.2, 0.3], [0.4, 0.6]], ("v", "w"))
    with pytest.raises(ValueError, match="users differ"):
        paired_score_differences({"a": left, "b": bad}, [2, 20], resamples=40)


def test_stream_alignment_rejects_missing_users_and_duplicate_items(tmp_path):
    rows = [
        dict(user_id="u", task=task, item_ids=["a", "b"], candidate_count=2)
        for task in ("new", "repeat")
    ]
    for name in ("a", "b"):
        pq.write_table(pa.Table.from_pylist(rows), tmp_path / f"{name}.parquet")
    sources = {name: dict(path=tmp_path / f"{name}.parquet") for name in ("a", "b")}
    assert len(list(recommendation_batches(sources, 2))) == 1
    rows[1]["item_ids"] = ["a", "a"]
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "b.parquet")
    with pytest.raises(ValueError, match="unique"):
        list(recommendation_batches(sources, 2))
    pq.write_table(pa.Table.from_pylist(rows[:1]), tmp_path / "b.parquet")
    with pytest.raises(ValueError, match="population mismatch"):
        list(recommendation_batches(sources, 2))


def test_stream_aligns_gpu_task_batches_with_cpu_interleaved_order(tmp_path):
    cpu = [
        dict(user_id=user, task=task, item_ids=["a", "b"], candidate_count=2)
        for user in ("u", "v", "w")
        for task in ("new", "repeat")
    ]
    gpu = sorted(cpu, key=lambda row: (row["task"], row["user_id"]))
    sources = {}
    for name, rows in (("cpu", cpu), ("gpu", gpu)):
        path = tmp_path / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        sources[name] = dict(path=path)
    actual = list(recommendation_batches(sources, 2))
    assert [len(batch) for batch in actual] == [4, 2]
    assert all(left == right for batch in actual for left, right in batch)


def test_cli_small_full_population_provenance_no_overwrite_and_null_details(tmp_path, monkeypatch):
    from src.infrastructure.research.configuration import load_settings
    from src.interfaces.cli import audit, evaluate

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    sources = {}
    monkeypatch.setattr(evaluate, "render", lambda *args: None)
    monkeypatch.setattr(audit, "render", lambda *args: [])
    for basis in ("play_count", "user_count"):
        raw = load_settings(Path("configs/popularity-plays.json"))
        raw["model"]["parameters"] = dict(basis=basis)
        raw["experiment"].update(
            validation_dataset=str(bundle / "validation"),
            features=str(bundle / "features.json"),
            output=str(tmp_path / basis),
        )
        path = tmp_path / f"{basis}.json"
        path.write_text(json.dumps(raw))
        sources[basis] = evaluate.run(path)
    output = audit.run(tmp_path / "audit", sources, backend="cpu", batch_users=2)
    result = json.loads((output / "result.json").read_text())
    assert result["population"]["users"] == 4
    assert result["status"] == "succeeded"
    assert len(pq.read_table(output / "users.parquet")) == 4 * 2 * 3
    assert any(
        value is None
        for vector in pq.read_table(output / "users.parquet")["union_recall"].to_pylist()
        for value in vector
    )
    assert (output / "config.json").exists() and (output / "metrics.csv").exists()
    assert not list(output.glob("*.partial"))
    with pytest.raises(FileExistsError):
        audit.run(output, sources, backend="cpu")
    path = sources["play_count"] / "recommendations.parquet"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_sources(sources)


def test_null_only_parquet_batch_keeps_float_list_schema(tmp_path):
    from src.interfaces.cli.audit import AuditDetails

    writer = AuditDetails(tmp_path / "users.parquet")
    row = dict(user_id="u", task="new", window_days=90, positive_items=0, union_recall=[None])
    writer.add(row)
    writer.flush()
    writer.add({**row, "user_id": "v", "positive_items": 1, "union_recall": [0.5]})
    writer.close()
    assert pq.read_table(writer.path)["union_recall"].to_pylist() == [[None], [0.5]]


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)
def test_cuda_matches_cpu_for_complementarity_and_paired_intervals(tmp_path):
    import cupy as cp

    from src.infrastructure.research.complementarity import percentile_interval

    for columns in (1, 2, 19):
        values = np.linspace(-0.5, 0.8, 40 * columns).reshape(40, columns)
        np.testing.assert_allclose(
            cp.asnumpy(percentile_interval(cp.asarray(values), cp)),
            np.quantile(values, [0.025, 0.975], axis=0),
            atol=1e-14,
        )

    features = {"0": {"rock": 1}, "1": {"jazz": 1}, "2": {"rock": 1}}
    analyzers = [
        Complementarity(
            ["a", "b"],
            features,
            list("0123"),
            ks=(2, 3),
            xp=xp,
            popularity_strata=[0, 0, 1, 1],
        )
        for xp in (np, cp)
    ]
    for audit in analyzers:
        audit.add("new", fixture_rankings(), {90: [[0, 2, 3], [], []]}, history_strata=[0, 0, 1])
    for left, right in zip(*(a.finish() for a in analyzers), strict=True):
        for key in left:
            assert (
                right[key] == pytest.approx(left[key])
                if isinstance(left[key], float)
                else right[key] == left[key]
            )
    from src.infrastructure.evaluation.gpu_metrics import GPUDiversity

    rankings = cp.asarray(fixture_rankings())
    audited = analyzers[1].finish()
    for position, name in enumerate(("a", "b")):
        reference = GPUDiversity(features, list("0123"), dict.fromkeys("0123", 1), 3, (2, 3))
        reference.add(
            "new", rankings[:, position], cp.asnumpy((rankings[:, position] >= 0).sum(axis=1))
        )
        for row in reference.finish(dict(new=4)):
            actual = metric(
                audited, "individual_ild", left=name, right=None, k=row["k"], window=None
            )
            assert actual["n_evaluated"] == row["ild_evaluated"]
            assert actual["mean"] == pytest.approx(row["ild"], abs=1e-12)
    sources = {
        "a": score_source(tmp_path / "a.parquet", [[0.0, 0.1], [0.2, 0.4]]),
        "b": score_source(tmp_path / "b.parquet", [[0.2, 0.3], [0.4, 0.6]]),
    }
    cpu = paired_score_differences(sources, [2, 20], xp=np, resamples=40)
    gpu = paired_score_differences(sources, [2, 20], xp=cp, resamples=40)
    for left, right in zip(cpu, gpu, strict=True):
        for key in left:
            assert (
                right[key] == pytest.approx(left[key])
                if isinstance(left[key], float)
                else right[key] == left[key]
            )


def test_paired_reference_limits_comparisons_without_changing_intervals(tmp_path):
    sources = {
        name: score_source(tmp_path / f"{name}.parquet", values)
        for name, values in {
            "a": [[0.0, 0.1], [0.2, 0.4]],
            "b": [[0.2, 0.3], [0.4, 0.6]],
            "c": [[0.3, 0.4], [0.5, 0.7]],
        }.items()
    }
    full = paired_score_differences(sources, [2, 20], resamples=40)
    reference = paired_score_differences(sources, [2, 20], resamples=40, reference="a")
    assert reference == [r for r in full if r["model_left"] == "a"]
    with pytest.raises(ValueError, match="first explicit source"):
        paired_score_differences(sources, [2, 20], reference="b")


def test_paired_fixed_seeds_are_averaged_per_user_without_pseudoreplication(tmp_path):
    first = score_source(tmp_path / "s1.parquet", [[0.0, 0.2], [0.4, 0.6]])
    second = score_source(tmp_path / "s2.parquet", [[0.2, 0.4], [0.6, 0.8]])
    other = score_source(tmp_path / "other.parquet", [[0.3, 0.5], [0.7, 0.9]])
    sources = {"a": {**first, "scores": [first["scores"], second["scores"]]}, "b": other}
    estimates = []
    rows = paired_score_differences(sources, [2, 20], resamples=40, estimates=estimates)
    for row in rows:
        assert row["mean"] == pytest.approx(0.2)
        assert row["n_evaluated"] == 2
        if row["k"] == 20:
            assert row["ci_low"] == pytest.approx(0.2)
            assert row["ci_high"] == pytest.approx(0.2)
    sources["a"]["scores"] = [first["scores"], first["scores"]]
    with pytest.raises(ValueError, match="distinct"):
        paired_score_differences(sources, [2, 20], resamples=40)
    estimate = next(
        r for r in estimates if r["condition"] == "a" and r["metric"] == "recall" and r["k"] == 20
    )
    assert estimate["mean"] == pytest.approx(0.5)
    assert estimate["n_evaluated"] == 2
    assert estimate["ci_low"] == pytest.approx(0.3)
    assert estimate["ci_high"] == pytest.approx(0.7)
