import copy

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def test_paper_groups_reject_mixed_solver_and_phase():
    from src.infrastructure.research.als_report import select_groups

    records = []
    names = [
        "32/validation",
        "64/validation",
        "128/validation",
        "bridge/f256-s0",
        "bridge/f512-s0",
        "1024",
    ]
    names += [f"{f}/test" for f in (32, 64, 128, 256, 512)]
    for name in names:
        phase = "test" if name.endswith("test") else "validation"
        record = dict(
            condition=name,
            seed=0,
            parameters=dict(use_gpu=True, cg_steps=20),
            saved=dict(
                manifest=dict(phase=phase),
                features_sha256="f",
                execution=dict(evaluation_device="cuda", solver="cg20"),
            ),
            result=dict(phase=phase, population=dict(users=3), primary=dict(ks=[2, 20])),
        )
        records.append(record)
    assert len(select_groups(records)["validation"]) == 6
    bad = copy.deepcopy(records)
    bad[0]["saved"]["execution"]["solver"] = "exact"
    with pytest.raises(ValueError, match="Incomparable"):
        select_groups(bad)
    bad = copy.deepcopy(records)
    bad[0]["result"]["phase"] = "test"
    with pytest.raises(ValueError, match="stated phase"):
        select_groups(bad)


def test_score_audit_recomputes_individual_means_and_detects_tampering(tmp_path):
    from src.infrastructure.research.als_report import score_audit

    pq.write_table(
        pa.Table.from_pylist(
            [
                dict(
                    user_id=u,
                    task="discovery",
                    metric="recall",
                    window_days=90,
                    day_weight=-1.0,
                    values=v,
                )
                for u, v in (("u1", [0, 0.5]), ("u2", [0.5, 1]))
            ]
        ),
        tmp_path / "scores.parquet",
    )
    record = dict(
        result_path=str(tmp_path / "result.json"),
        result=dict(
            primary=dict(ks=[2, 3]),
            population=dict(users=3),
            artifacts=dict(scores=dict(path="scores.parquet")),
            accuracy=[
                dict(
                    task="discovery",
                    metric="recall",
                    window_days=90,
                    day_weight=None,
                    k=k,
                    mean=m,
                    n_evaluated=2,
                    n_excluded=1,
                )
                for k, m in ((2, 0.25), (3, 0.75))
            ],
        ),
    )
    assert score_audit(record) == 2
    record["result"]["accuracy"][0]["mean"] = 0.26
    with pytest.raises(ValueError, match="mean mismatch"):
        score_audit(record)


def test_common_ild_excludes_missing_features_from_both_conditions(tmp_path):
    from src.infrastructure.datasets.features import write_features
    from src.infrastructure.datasets.listening_data import file_sha256
    from src.infrastructure.research.als_report import common_ild

    features = tmp_path / "features.json"
    write_features(
        features,
        dataset_id="toy",
        snapshot_id="snapshot",
        features={"a": {"rock": 1}, "b": {"jazz": 1}},
        source={},
    )
    records = []
    for factors, missing in ((32, "u3"), (64, "u1")):
        directory = tmp_path / str(factors)
        directory.mkdir()
        pq.write_table(
            pa.Table.from_pylist(
                [
                    dict(
                        user_id=u,
                        task=task,
                        item_ids=["a", "c" if task == "new" and u == missing else "b"],
                        candidate_count=2,
                    )
                    for u in ("u1", "u2", "u3")
                    for task in ("new", "repeat")
                ]
            ),
            directory / "recommendations.parquet",
        )
        records.append(
            dict(
                factors=factors,
                phase="validation",
                result_path=str(directory / "result.json"),
                saved=dict(
                    settings=dict(features=str(features)),
                    features_sha256=file_sha256(features),
                    manifest=dict(dataset_id="toy", source=dict(snapshot_id="snapshot")),
                ),
                result=dict(
                    population=dict(users=3),
                    primary=dict(ks=[2]),
                    artifacts=dict(
                        recommendations=dict(path="recommendations.parquet"),
                        scores=dict(path="unused.parquet"),
                    ),
                ),
            )
        )
    rows = common_ild(records)
    assert [r["n_evaluated"] for r in rows if r["task"] == "new"] == [1, 1]
    assert [r["n_evaluated"] for r in rows if r["task"] == "repeat"] == [3, 3]
    assert all(r["mean"] == pytest.approx(1) for r in rows)


def test_discrete_factor_plot_keeps_null_and_measured_zero():
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.als_report_figures import factor_plot

    frame = pd.DataFrame(
        dict(
            factors=[32, 64, 128],
            mean=[0, None, 0.2],
            ci_low=[0, None, 0.1],
            ci_high=[0, None, 0.3],
        )
    )
    fig = factor_plot(frame, "Recall@20 (%)", 100, interval=True)
    try:
        ax = fig.axes[0]
        assert not ax.get_title() and ax.get_xscale() == "log"
        assert list(ax.get_xticks()) == [32, 64, 128]
        assert ax.lines[0].get_linestyle() == "None"
        assert ax.lines[0].get_ydata()[0] == 0
        assert np.isnan(ax.lines[0].get_ydata()[1])
        assert factor_plot(frame.assign(mean=None), "Unmeasured") is None
    finally:
        plt.close(fig)


def test_sensitivity_heatmaps_preserve_unmeasured_cells():
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.als_report_figures import sensitivity_figures
    from src.interfaces.notebook.evaluation_figures import ACCURACY_NAMES

    records = []
    for alpha, reg in ((0.1, 0.1), (1, 0.1), (1, 1)):
        for seed in (42, 43, 44):
            accuracy = [
                dict(
                    task=task,
                    metric=metric,
                    window_days=90,
                    day_weight=None,
                    k=20,
                    mean=0 if alpha == 0.1 else seed / 1000,
                )
                for task, metric in ACCURACY_NAMES
            ]
            records.append(
                dict(
                    phase="validation",
                    condition=f"sensitivity/{alpha}-{reg}-{seed}",
                    alpha=alpha,
                    regularization=reg,
                    seed=seed,
                    factors=256,
                    parameters=dict(factors=256, alpha=alpha, regularization=reg),
                    saved=dict(
                        features_sha256="features",
                        manifest=dict(files=dict(learning=dict(sha256="input"))),
                    ),
                    result=dict(accuracy=accuracy),
                )
            )
    drawn = sensitivity_figures(records)
    try:
        ax = drawn["recall_sensitivity"].axes[0]
        image = ax.images[0]
        assert not ax.get_title() and image.get_interpolation() == "nearest"
        assert image.get_array().mask[1, 0]
        assert image.get_array()[0, 0] == 0
        assert image.get_array()[0, 1] == pytest.approx(4.3)
    finally:
        plt.close("all")
