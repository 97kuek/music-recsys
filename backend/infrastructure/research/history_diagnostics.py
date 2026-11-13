"""Paired saved-score diagnostics; train-only history strata and no policy fitting."""

import os
import time
from pathlib import Path

import numpy as np

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.provenance import source_hash
from backend.infrastructure.research.als_report import write_csv
from backend.infrastructure.research.hybrid_config import read, reference, verify_references
from backend.infrastructure.research.metrics import metric_rows
from backend.infrastructure.research.personal_metrics import interval
from backend.infrastructure.research.results import verify_artifacts
from backend.infrastructure.research.store import atomic_json


def paired_summary(left, right, *, xp, tolerance=1e-12):
    difference = right - left
    improved = (difference > tolerance).sum(axis=0)
    worsened = (difference < -tolerance).sum(axis=0)
    return left.mean(axis=0), right.mean(axis=0), difference.mean(axis=0), improved, worsened


def run(root, *, xp, execution, render):
    import duckdb
    import pyarrow.parquet as pq

    root = Path(root)
    output = root / "recency-log-count" / "diagnostics"
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise FileExistsError("Saved diagnostics must be preserved")
    started, processing = time.perf_counter(), source_hash()
    evidence, rows, overall, joint = {}, [], [], []
    cuts = None
    partial.mkdir()
    with (partial / "run.log").open("x") as log:
        for phase in ("validation", "test"):
            paths = [root / name for name in ("last-play", "recency-log-count")]
            if phase == "test":
                paths = [p / "test" for p in paths]
            saved = [read(p / "config.json") for p in paths]
            results = [read(p / "result.json") for p in paths]
            for i, (p, r, c) in enumerate(zip(paths, results, saved, strict=True)):
                if r["status"] != "succeeded" or r["phase"] != phase:
                    raise ValueError("Completed matching phase required")
                if any(c[k] != saved[0][k] for k in ("manifest", "features_sha256")):
                    raise ValueError("Input identities differ")
                if (
                    r["population"] != results[0]["population"]
                    or r["primary"] != results[0]["primary"]
                ):
                    raise ValueError("Population or evaluation differs")
                verify_artifacts(p, r["artifacts"])
                evidence.update({f"{phase}/{i}/{k}": v for k, v in reference(p).items()})
                evidence[f"{phase}/{i}/scores"] = dict(
                    path=str(p / "scores.parquet"), sha256=file_sha256(p / "scores.parquet")
                )
                overall.extend(
                    dict(x, phase=phase, condition=p.parent.name if phase == "test" else p.name)
                    for x in metric_rows(r)
                )
            learning = Path(saved[0]["settings"][f"{phase}_dataset"]) / "learning.parquet"
            digest = file_sha256(learning)
            if digest != saved[0]["manifest"]["files"]["learning"]["sha256"]:
                raise ValueError("Learning hash differs")
            evidence[f"{phase}/learning"] = dict(path=str(learning), sha256=digest)
            with duckdb.connect(config={"threads": 2, "memory_limit": "8GB"}) as c:
                history = (
                    c.execute(
                        "SELECT user_id,count(*) AS tracks FROM read_parquet(?) GROUP BY user_id ORDER BY user_id",
                        [str(learning)],
                    )
                    .fetchdf()
                    .set_index("user_id")
                )
            if len(history) != results[0]["population"]["users"]:
                raise ValueError("All learning users required")
            if cuts is None:
                cuts = np.quantile(history.tracks.to_numpy(), [0.25, 0.5, 0.75]).tolist()
            tables = [pq.read_table(p / "scores.parquet") for p in paths]
            keys = ["user_id", "task", "window_days", "day_weight", "metric"]
            if not tables[0].select(keys).equals(tables[1].select(keys)):
                raise ValueError("Score user alignment differs")
            meta = tables[0].select(keys).to_pandas()
            values = []
            ks = results[0]["primary"]["ks"]
            for t in tables:
                flat = t["values"].combine_chunks()
                if not np.all(np.diff(flat.offsets.to_numpy()) == len(ks)):
                    raise ValueError("Score K shape differs")
                values.append(flat.values.to_numpy().reshape(-1, len(ks)))
            task_differences = {}
            for key, indices in meta.groupby(keys[1:]).indices.items():
                task, window, weight, metric = key
                users = meta.iloc[indices].user_id.tolist()
                if len(users) != len(set(users)):
                    raise ValueError("Duplicate score user")
                groups = np.searchsorted(cuts, history.loc[users, "tracks"].to_numpy(), side="left")
                left, right = (xp.asarray(v[indices]) for v in values)
                primary = task == "repeat" and window == 90 and weight in (-1, 1)
                if primary:
                    diff = right[:, ks.index(20)] - left[:, ks.index(20)]
                    task_differences[metric] = (users, xp.asnumpy(diff) if xp is not np else diff)
                for group in (-1, 0, 1, 2, 3):
                    mask = np.ones(len(users), dtype=bool) if group == -1 else groups == group
                    n = int(mask.sum())
                    total = (
                        len(history)
                        if group == -1
                        else int(
                            (
                                np.searchsorted(cuts, history.tracks.to_numpy(), side="left")
                                == group
                            ).sum()
                        )
                    )
                    if not n:
                        raise ValueError("Empty diagnostic stratum; explicit handling required")
                    a, b = left[xp.asarray(mask)], right[xp.asarray(mask)]
                    statistics = paired_summary(a, b, xp=xp)
                    bounds = interval((b - a)[:, [ks.index(20)]], xp=xp) if primary else None
                    for j, k in enumerate(ks):
                        rows.append(
                            dict(
                                phase=phase,
                                task=task,
                                window_days=int(window),
                                day_weight=None if weight < 0 else float(weight),
                                metric=metric,
                                k=k,
                                stratum="all" if group == -1 else f"Q{group + 1}",
                                left_mean=float(statistics[0][j]),
                                right_mean=float(statistics[1][j]),
                                difference=float(statistics[2][j]),
                                improved=int(statistics[3][j]),
                                worsened=int(statistics[4][j]),
                                tied=n - int(statistics[3][j]) - int(statistics[4][j]),
                                n_evaluated=n,
                                n_excluded=total - n,
                                population=total,
                                ci_low=float(bounds[0, 0])
                                if bounds is not None and k == 20
                                else None,
                                ci_high=float(bounds[1, 0])
                                if bounds is not None and k == 20
                                else None,
                            )
                        )
            u, a = task_differences["graded_ndcg"]
            v, b = task_differences["repeat_recall"]
            if u != v:
                raise ValueError("Joint diagnostic users differ")

            def signs(x):
                return np.where(x > 1e-12, "improved", np.where(x < -1e-12, "worsened", "tied"))

            sa, sb = signs(a), signs(b)
            for x in ("improved", "worsened", "tied"):
                for y in ("improved", "worsened", "tied"):
                    joint.append(
                        dict(
                            phase=phase,
                            ndcg=x,
                            recall=y,
                            users=int(((sa == x) & (sb == y)).sum()),
                            n_evaluated=len(u),
                        )
                    )
            log.write(f"Verified {phase}: {len(history)} learning users\n")
            log.flush()
            print(f"Verified {phase}: {len(history)} learning users", flush=True)
    for row in rows:
        if row["ci_low"] is not None and (
            not np.isfinite([row["ci_low"], row["ci_high"]]).all() or row["ci_low"] > row["ci_high"]
        ):
            raise ValueError("Invalid confidence interval")
    write_csv(partial / "metrics.csv", rows)
    write_csv(partial / "overall.csv", overall)
    write_csv(partial / "joint.csv", joint)
    render(partial, rows)
    atomic_json(
        partial / "config.json",
        dict(
            source_sha256=processing,
            evidence=evidence,
            execution=execution,
            history_tracks_quartiles=cuts,
            cut_definition="Validation all learning users; <= cut belongs to lower group; reused unchanged on Test",
            tolerance=1e-12,
            selection="not_selected",
            intervals="2000 paired-user bootstrap; repeat 90d K20; no multiplicity correction",
            scope="paired accuracy diagnostics, not subgroup diversity or a trained selector; four-aspect overall measurements in overall.csv and original comparison",
        ),
    )
    verify_references([evidence])
    if processing != source_hash():
        raise ValueError("Source changed")
    atomic_json(
        partial / "result.json",
        dict(
            status="succeeded",
            source_sha256=processing,
            elapsed_seconds=time.perf_counter() - started,
            rows=len(rows),
            external_api="not_run",
            artifacts={
                p.name: dict(path=p.name, sha256=file_sha256(p))
                for p in partial.iterdir()
                if p.is_file()
            },
        ),
    )
    os.rename(partial, output)
    return output
