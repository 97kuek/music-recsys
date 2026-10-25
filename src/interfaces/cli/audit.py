"""Audit saved Validation top-20 overlap, unique hits, union Recall and common ILD."""

import argparse
import csv
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from src.infrastructure.datasets.features import load_features
from src.infrastructure.datasets.full_io import DetailWriter, user_rows
from src.infrastructure.datasets.listening_data import file_sha256, read_bundle
from src.infrastructure.research.complementarity import (
    DEFAULT_SOURCES,
    Complementarity,
    load_sources,
    paired_score_differences,
    recommendation_batches,
    tie_safe_quartiles,
)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def run(output, sources, *, backend="cuda", batch_users=64, publication=False):
    if batch_users < 1:
        raise ValueError("batch_users must be positive")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Audit output must be new; saved artifacts are never overwritten")
    loaded, common = load_sources(sources)
    first = next(iter(loaded.values()))["config"]
    manifest, paths = read_bundle(first["settings"]["validation_dataset"])
    if manifest != common["manifest"]:
        raise ValueError("Current bundle differs from saved source manifest")
    features_path = Path(first["settings"]["features"])
    if file_sha256(features_path) != common["features_sha256"]:
        raise ValueError("Current feature artifact differs from saved sources")
    features = load_features(
        features_path,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    import duckdb

    with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as connection:
        # listening-v2 learning contains exactly one row per user/item, so row counts
        # equal distinct observed tracks/users without allocating another 42M-pair set.
        connection.read_parquet(str(paths["learning"])).create_view("learning")
        user_counts = connection.execute(
            "SELECT user_id,count(*) FROM learning GROUP BY user_id ORDER BY user_id"
        ).fetchall()
        item_counts = connection.execute(
            "SELECT item_id,count(*) FROM learning GROUP BY item_id ORDER BY item_id"
        ).fetchall()
    population_users = [row[0] for row in user_counts]
    items = [row[0] for row in item_counts]
    history_groups, history_metadata = tie_safe_quartiles([row[1] for row in user_counts])
    popularity_groups, popularity_metadata = tie_safe_quartiles([row[1] for row in item_counts])
    history_by_user = dict(zip(population_users, history_groups, strict=True))
    names = list(loaded)
    stratification = dict(
        history=dict(definition="distinct learning tracks per user", **history_metadata),
        popularity=dict(definition="distinct learning users per track", **popularity_metadata),
        tie_policy="empirical inverse-CDF boundaries; ties placed together in lower band; empty bands retained",
        stratified_pairs=[[names[0], other] for other in names[1:]],
        pair_scope="stratified pairs compare the first explicit source against each other source; overall retains all pairs",
        population="history: users assigned to the history band; popularity: all learning users, truth and hits restricted to that item band",
        plots="overall curves only; all stratified values and denominators in metrics.csv/result.json",
    )
    xp = np
    execution = dict(backend=backend, batch_users=batch_users, dtype="float64")
    if backend == "cuda":
        import cupy as cp

        from src.infrastructure.recommenders.gpu_runtime import gpu_metadata, require_idle_gpu

        require_idle_gpu()
        execution.update(**gpu_metadata(), cupy=cp.__version__)
        xp = cp
    elif backend != "cpu":
        raise ValueError("Unknown backend")
    index = {item: i for i, item in enumerate(items)}
    ks = tuple(common["evaluation"]["ks"])
    windows = tuple(common["evaluation"]["window_days"])
    analyzer = Complementarity(loaded, features, items, ks, xp, popularity_strata=popularity_groups)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    write_json(
        output / "config.json",
        dict(
            format="music-recsys-complementarity-config-v1",
            sources={name: source["provenance"] for name, source in loaded.items()},
            inputs=common,
            execution=execution,
            stratification=stratification,
            implementation_sha256=file_sha256(Path(__file__)),
            analysis_sha256=file_sha256(
                Path(__file__).parents[2] / "infrastructure/research/complementarity.py"
            ),
            dependency_lock_sha256=file_sha256(Path(__file__).parents[3] / "uv.lock"),
        ),
    )
    processed = 0
    started = time.perf_counter()
    observations = iter(user_rows(paths["observations"]))
    next_observation = next(observations, None)
    learning = iter(population_users)
    writer = AuditDetails(output / "users.parquet.partial")
    try:
        with (output / "run.log").open("x") as log:
            for batch in recommendation_batches(loaded, batch_users):
                by_task = {task: [] for task in ("new", "repeat")}
                truths = {task: {w: [] for w in windows} for task in by_task}
                users = []
                for pair in range(0, len(batch), 2):
                    user = batch[pair][0]["user_id"]
                    learned_user = next(learning, None)
                    if user != learned_user:
                        raise ValueError(
                            "Saved recommendations differ from full learning population"
                        )
                    if next_observation is not None and next_observation[0] < user:
                        raise ValueError(
                            "Observations contain user outside recommendation population"
                        )
                    observed = []
                    if next_observation is not None and next_observation[0] == user:
                        observed = next_observation[1]
                        next_observation = next(observations, None)
                    users.append(user)
                    for offset, task in enumerate(by_task):
                        request = batch[pair + offset]
                        ranking = np.full((len(loaded), 20), -1, dtype=np.int32)
                        for m, row in enumerate(request):
                            ranking[m, : len(row["item_ids"])] = [index[i] for i in row["item_ids"]]
                        by_task[task].append(ranking)
                        for window in windows:
                            truths[task][window].append(
                                sorted(
                                    {
                                        index[r[1]]
                                        for r in observed
                                        if r[2] == window and (r[3] > 0) == (task == "repeat")
                                    }
                                )
                            )
                for task, rankings in by_task.items():
                    details = analyzer.add(
                        task,
                        np.stack(rankings),
                        truths[task],
                        history_strata=[history_by_user[user] for user in users],
                    )
                    for window, (counts, values) in details.items():
                        for user, count, vector in zip(users, counts, values, strict=True):
                            writer.add(
                                dict(
                                    user_id=user,
                                    task=task,
                                    window_days=window,
                                    positive_items=int(count),
                                    history_stratum=f"Q{history_by_user[user] + 1}",
                                    union_recall=[
                                        float(v) if math.isfinite(v) else None for v in vector
                                    ],
                                )
                            )
                processed += len(users)
                if processed % 1024 < len(users):
                    message = f"Audited {processed:,}/{common['population']['users']:,} users"
                    print(message, flush=True)
                    log.write(message + "\n")
                    log.flush()
            if next(learning, None) is not None or next_observation is not None:
                raise ValueError("Recommendation population incomplete")
            if processed != common["population"]["users"]:
                raise ValueError("Population mismatch")
            log.write(f"Population complete: {processed} users; paired bootstrap follows\n")
    finally:
        writer.close()
    rows = analyzer.finish()
    verify_individual_ild(rows, loaded)
    rows.extend(paired_score_differences(loaded, list(ks), windows=windows, xp=xp))
    for row in rows:
        row.setdefault("stratum_kind", "all")
        row.setdefault("stratum", "all")
        row.setdefault("ci_low", None)
        row.setdefault("ci_high", None)
    with (output / "metrics.csv").open("x", newline="") as stream:
        csv_writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        csv_writer.writeheader()
        csv_writer.writerows(rows)
    os.replace(output / "users.parquet.partial", output / "users.parquet")
    if publication:
        from src.interfaces.notebook.audit_figures import render as publication_render

        figures = publication_render(output, rows)
    else:
        figures = render(output, rows)
    write_json(
        output / "result.json.partial",
        dict(
            format="music-recsys-complementarity-v1",
            status="succeeded",
            phase="validation",
            population=common["population"],
            ks=ks,
            window_days=windows,
            models=list(loaded),
            metrics=rows,
            execution=execution,
            stratification=stratification,
            efficiency=dict(evaluation_seconds=time.perf_counter() - started),
            definitions=dict(
                candidate_scope="union of saved per-model top-K, K<=20; not a top-100 candidate experiment",
                union_recall="per-user distinct positive union / all eligible positives; excludes positive-free users",
                recall_gain_over_best="per-user union Recall minus maximum of that user's two Recalls; oracle diagnostic only",
                unique_hits="positive items retrieved by one of a pair and absent from the other's top-K",
                common_ild="equal-user cosine ILD on users valid for every compared model at that K; no imputation",
                pair_common_ild="left/right ILD on users valid for both models at K; individual_ild preserves source denominator",
                paired_difference="right minus left, matched users; mean at all windows/K2-20, graded weight1; 95% user bootstrap CI only window90/dayweight1/K10,20, 2000 resamples, seed42; not primary metric selection",
                overlap="overlap_items averages all users; Jaccard excludes empty recommendation unions",
                selection="diagnostic only; no primary metric selection, model adoption, or final Test",
                strata="history bands condition on users; popularity bands condition truth and hit denominator on training item popularity; no future observations define bands",
                stratified_oracle="union Recall and recall_gain_over_best are candidate/oracle diagnostics within each band; not achievable reranking scores",
            ),
            artifacts=dict(
                users=dict(path="users.parquet", sha256=file_sha256(output / "users.parquet"))
            ),
            figures=figures,
        ),
    )
    os.replace(output / "result.json.partial", output / "result.json")
    with (output / "run.log").open("a") as log:
        log.write(f"SUCCEEDED: {processed} users\n")
    return output


def verify_individual_ild(rows, sources):
    """Refuse publication when prefix ILD does not reproduce the source denominator/value."""
    for row in rows:
        if row["metric"] != "individual_ild" or row.get("stratum_kind", "all") != "all":
            continue
        expected = next(
            item
            for item in sources[row["model_left"]]["diversity"]
            if item["task"] == row["task"] and item["k"] == row["k"]
        )
        if expected["ild_evaluated"] != row["n_evaluated"]:
            raise ValueError("Source ILD valid-user denominator was not reproduced")
        actual, previous = row["mean"], expected["ild"]
        if (actual is None) != (previous is None) or (
            actual is not None and not math.isclose(actual, previous, rel_tol=1e-8, abs_tol=1e-10)
        ):
            raise ValueError("Source ILD value was not reproduced")


class AuditDetails(DetailWriter):
    """Explicit list<float64> schema preserves null-only initial batches."""

    def flush(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        if self.rows:
            schema = pa.schema(
                [
                    ("user_id", pa.string()),
                    ("task", pa.string()),
                    ("window_days", pa.int64()),
                    ("positive_items", pa.int64()),
                    ("history_stratum", pa.string()),
                    ("union_recall", pa.list_(pa.float64())),
                ]
            )
            table = pa.Table.from_pylist(self.rows, schema=schema)
            if self.writer is None:
                self.writer = pq.ParquetWriter(self.path, schema, compression="zstd")
            self.writer.write_table(table)
            self.rows.clear()


def render(output, rows):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures = []
    specifications = sorted({(r["metric"], r["task"], r["window_days"] or 0) for r in rows})
    for metric, task, window in specifications:
        selected = [
            r
            for r in rows
            if r["metric"] == metric
            and r["task"] == task
            and (r["window_days"] or 0) == window
            and r["mean"] is not None
            and r.get("stratum_kind", "all") == "all"
        ]
        if not selected:
            continue
        figure, axis = plt.subplots(figsize=(13, 7))
        groups = {}
        for row in selected:
            key = row["model_left"], row["model_right"]
            groups.setdefault(key, []).append(row)
        for key, values in groups.items():
            values.sort(key=lambda row: row["k"])
            label = " / ".join(str(v) for v in key if v is not None)
            axis.plot(
                [r["k"] for r in values], [r["mean"] for r in values], alpha=0.65, label=label
            )
        axis.set(xlabel="K", ylabel=metric, title=f"{metric} | {task} | {window or 'all'} days")
        axis.grid(alpha=0.2)
        axis.legend(loc="center left", bbox_to_anchor=(1, 0.5), fontsize=5)
        figure.tight_layout()
        name = f"{metric}_{task}" + (f"_{window}" if window else "") + ".png"
        figure.savefig(output / name, dpi=130)
        plt.close(figure)
        figures.append(name)
    return figures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("data/experiments/comparison/complementarity")
    )
    parser.add_argument("--backend", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--batch-users", type=int, default=64)
    parser.add_argument("--source", action="append", metavar="NAME=RESULT_DIRECTORY")
    parser.add_argument("--publication", action="store_true")
    args = parser.parse_args()
    sources = (
        dict(arg.split("=", 1) for arg in args.source)
        if args.source
        else {
            name: Path("data/experiments") / directory
            for name, directory in DEFAULT_SOURCES.items()
        }
    )
    run(
        args.output,
        sources,
        backend=args.backend,
        batch_users=args.batch_users,
        publication=args.publication,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
