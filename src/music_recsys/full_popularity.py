"""全学習ユーザーのPopularity Validation / 集約履歴を順次読み、個別結果をParquet保存。"""

import argparse
import json
import os
import time
from collections import defaultdict
from dataclasses import asdict
from itertools import groupby, islice
from pathlib import Path

import numpy as np

from music_recsys.experiment_store import atomic_json
from music_recsys.listening_data import file_sha256, validate_bundle
from music_recsys.streaming_metrics import DiversityAccumulator, accuracy_vectors


def user_rows(path):
    import pyarrow.parquet as pq

    def rows():
        for batch in pq.ParquetFile(path).iter_batches(batch_size=32768):
            yield from zip(*(column.to_pylist() for column in batch.columns), strict=True)

    for user, records in groupby(rows(), key=lambda row: row[0]):
        yield user, list(records)


class DetailWriter:
    def __init__(self, path):
        self.path, self.rows, self.writer = path, [], None

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= 4096:
            self.flush()

    def flush(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        if self.rows:
            table = pa.Table.from_pylist(self.rows)
            if self.writer is None:
                self.writer = pq.ParquetWriter(self.path, table.schema, compression="zstd")
            self.writer.write_table(table)
            self.rows.clear()

    def close(self):
        self.flush()
        if self.writer is not None:
            self.writer.close()


def run(config_path):
    import duckdb

    from music_recsys.evaluation.benchmark import _source_sha256
    from music_recsys.experiment_search import load_study_config, study_lock
    from music_recsys.features import load_features

    config = load_study_config(config_path)
    if (
        config.model != "popularity"
        or config.protocol != "listening-v2"
        or config.parameters.keys() - {"basis"}
        or config.grid
    ):
        raise ValueError("full_popularity requires listening-v2 popularity with a fixed basis")
    from music_recsys.recommenders.popularity import validate_basis

    basis = validate_basis(config.parameters.get("basis", "play_count"))
    ordering = "sum(play_count)" if basis == "play_count" else "count(*)"
    output = config.output
    output.mkdir(parents=True, exist_ok=True)
    with study_lock(config):
        if (output / "result.json").exists():
            raise FileExistsError("結果は上書きしません / 新しい出力先を指定してください")
        with (output / "run.log").open("a") as log:

            def progress(message):
                print(message, flush=True)
                log.write(message + "\n")
                log.flush()

            progress("Validating full Validation bundle")
            manifest, paths, counts = validate_bundle(config.validation_dataset)
            if manifest["phase"] != "validation":
                raise ValueError("Validation only; Test is not opened")
            features = load_features(
                config.features,
                dataset_id=manifest["dataset_id"],
                snapshot_id=manifest["source"]["snapshot_id"],
            )
            started = time.perf_counter()
            with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as c:
                ranked = c.execute(
                    f"SELECT item_id,sum(play_count)::BIGINT,count(*) FROM read_parquet(?) GROUP BY item_id ORDER BY {ordering} DESC,item_id",
                    [str(paths["learning"])],
                ).fetchall()
            training_seconds = time.perf_counter() - started
            popularity = [row[0] for row in ranked]
            rank = {item: j for j, item in enumerate(popularity)}
            item_users = {item: users for item, _, users in ranked}
            atomic_json(
                output / "config.json",
                {
                    "format": "music-recsys-full-config-v1",
                    "settings": json.loads(json.dumps(asdict(config), default=str)),
                    "manifest": manifest,
                    "features_sha256": file_sha256(config.features),
                    "source_sha256": _source_sha256(),
                    "execution": "deterministic popularity once; no seed-dependent computation",
                    "population": counts,
                },
            )
            accuracy = defaultdict(
                lambda: {"sum": np.zeros(len(config.evaluation.ks)), "n": 0, "primary": []}
            )
            diversity = DiversityAccumulator(
                features, item_users, counts["users"], config.evaluation.ks
            )
            details = DetailWriter(output / "scores.parquet.partial")
            recommendations = DetailWriter(output / "recommendations.parquet.partial")
            observation_iter = iter(user_rows(paths["observations"]))
            next_observation = next(observation_iter, None)
            durations = []
            processed = 0
            progress(
                f"Evaluating {counts['users']:,} users / {counts['items']:,} tracks / {counts['events']:,} plays"
            )
            try:
                for user, history_rows in user_rows(paths["learning"]):
                    history = {row[1] for row in history_rows}
                    if next_observation is not None and next_observation[0] < user:
                        raise ValueError("observations contain user outside learning population")
                    observations = (
                        next_observation[1]
                        if next_observation is not None and next_observation[0] == user
                        else []
                    )
                    if observations:
                        next_observation = next(observation_iter, None)
                    for task in ("new", "repeat"):
                        candidate_count = (
                            len(history) if task == "repeat" else len(popularity) - len(history)
                        )
                        t = time.perf_counter()
                        ranking = (
                            sorted(history, key=rank.__getitem__)[:20]
                            if task == "repeat"
                            else list(islice((i for i in popularity if i not in history), 20))
                        )
                        elapsed = time.perf_counter() - t
                        if candidate_count:
                            durations.append(elapsed)
                        recommendations.add(
                            dict(
                                user_id=user,
                                task=task,
                                item_ids=ranking,
                                candidate_count=candidate_count,
                                latency_seconds=elapsed,
                            )
                        )
                        truth = [
                            (r[1], r[2], r[5], r[6])
                            for r in observations
                            if (r[3] > 0) == (task == "repeat")
                        ]
                        for key, values in accuracy_vectors(
                            ranking, truth, task=task, config=config.evaluation
                        ):
                            a = accuracy[key]
                            if values is not None:
                                a["sum"] += values
                                a["n"] += 1
                                if key[1] == config.evaluation.primary_window_days and key[2] in (
                                    None,
                                    config.evaluation.primary_day_weight,
                                ):
                                    a["primary"].append(values)
                                details.add(
                                    dict(
                                        user_id=user,
                                        task=key[0],
                                        window_days=key[1],
                                        day_weight=float(key[2]) if key[2] is not None else -1.0,
                                        metric=key[3],
                                        values=values.tolist(),
                                    )
                                )
                        diversity.add(task, ranking, candidate_count)
                    processed += 1
                    if processed % 1000 == 0:
                        progress(f"Evaluated {processed:,}/{counts['users']:,} users")
                if processed != counts["users"] or next_observation is not None:
                    raise ValueError("population mismatch")
            finally:
                details.close()
                recommendations.close()
            progress("Aggregating scores and user bootstrap intervals")
            rows = []
            for key, a in accuracy.items():
                ci = None
                if a["primary"]:
                    values = np.asarray(a["primary"])
                    rng = np.random.default_rng(42)
                    means = np.empty((2000, len(config.evaluation.ks)))
                    for b in range(len(means)):
                        means[b] = values[rng.integers(0, len(values), len(values))].mean(axis=0)
                    ci = np.quantile(means, [0.025, 0.975], axis=0)
                for index, k in enumerate(config.evaluation.ks):
                    rows.append(
                        dict(
                            task=key[0],
                            window_days=key[1],
                            day_weight=key[2],
                            metric=key[3],
                            k=k,
                            mean=float(a["sum"][index] / a["n"]) if a["n"] else None,
                            n_evaluated=a["n"],
                            n_excluded=counts["users"] - a["n"],
                            ci_low=float(ci[0, index]) if ci is not None else None,
                            ci_high=float(ci[1, index]) if ci is not None else None,
                        )
                    )
            beyond = diversity.finish(
                {
                    "new": sum(n < counts["users"] for n in item_users.values()),
                    "repeat": len(popularity),
                }
            )
            efficiency = dict(
                model="popularity",
                latency_mean=sum(durations) / len(durations) if durations else None,
                generation_calls=len(durations),
                training_seconds=training_seconds,
                input_tokens=None,
                output_tokens=None,
                api_cost=None,
                llm_calls=None,
                tool_calls=None,
            )
            artifacts = {}
            for name in ("scores", "recommendations"):
                path = output / f"{name}.parquet"
                os.replace(output / f"{name}.parquet.partial", path)
                artifacts[name] = {"path": path.name, "sha256": file_sha256(path)}
            document = {
                "format": "music-recsys-full-result-v1",
                "status": "succeeded",
                "phase": "validation",
                "model": "popularity",
                "popularity_basis": basis,
                "population": counts,
                "primary": asdict(config.evaluation),
                "accuracy": rows,
                "diversity": beyond,
                "efficiency": efficiency,
                "artifacts": artifacts,
                "definitions": {
                    "population": "all train-observed users; no future activity selection",
                    "accuracy": "equal user mean; users without positives excluded separately for each task/window",
                    "ranking": f"training {basis} descending, item ID ascending; new excludes history; repeat restricts to history",
                    "gain": "log2(1+play_count)+day_weight*log2(1+active_days)",
                    "coverage": "unique recommended items / union of task candidates",
                    "novelty": "-log2((distinct learning users for item+1)/(learning users+2))",
                    "ild": "mean pairwise cosine distance; all returned valid tracks require nonzero tags",
                    "ci": "user bootstrap, 2000 resamples, seed 42, primary window/weight only",
                    "details": "scores: K array follows config ks; day_weight=-1 means not applicable; absent user-condition has no positive truth; recommendations: all users and both tasks",
                    "latency": "optimized deterministic ranking retrieval, excludes I/O, training and scoring; cannot compare to old full-sort implementation without accounting for implementation change",
                    "usage": "unmeasured is null",
                },
            }
            render(config, document)
            atomic_json(output / "result.json", document)
            progress(f"SUCCEEDED: {processed:,} users; results at {output}")
    return output


def render(config, document):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    from music_recsys.evaluation_figures import (
        accuracy_figures,
        diversity_figures,
        efficiency_figures,
        observation_figures,
    )
    from music_recsys.listening_analysis import observation_distribution

    frame = pd.DataFrame(document["accuracy"])
    primary = frame[
        (frame.window_days == config.evaluation.primary_window_days)
        & (
            (frame.metric != "graded_ndcg")
            | (frame.day_weight == config.evaluation.primary_day_weight)
        )
    ]
    beyond = pd.DataFrame(document["diversity"])
    efficiency = pd.DataFrame([document["efficiency"]])
    observations = observation_distribution(config)
    document["observations"] = json.loads(observations.to_json(orient="records"))
    figures = accuracy_figures(primary)
    figures.update(diversity_figures(beyond))
    figures.update(efficiency_figures(efficiency))
    figures.update(observation_figures(observations))
    from music_recsys.evaluation_figures import accuracy_heatmaps

    for name, figure in figures.items():
        figure.savefig(config.output / f"{name}.png", dpi=160)
        plt.close(figure)
    heatmaps = accuracy_heatmaps(frame, config.evaluation.primary_day_weight)
    for name, figure in heatmaps.items():
        figure.savefig(config.output / f"{name}.png", dpi=160)
        plt.close(figure)
    document["figures"] = [f"{name}.png" for name in [*figures, *heatmaps]]
    from music_recsys.evaluation.benchmark import _source_sha256
    from music_recsys.evaluation_figures import prune_generated_figures

    prune_generated_figures(config.output, document["figures"])
    document["render_source_sha256"] = _source_sha256()
    tables = [
        frame.rename(columns={"mean": "value"}).assign(aspect="accuracy"),
        beyond.melt(
            id_vars=["task", "k"],
            value_vars=["ild", "catalog_coverage", "novelty"],
            var_name="metric",
            value_name="value",
        ).assign(aspect="diversity"),
        efficiency.melt(id_vars=["model"], var_name="metric", value_name="value").assign(
            aspect="efficiency"
        ),
        observations.rename(columns={"observation": "metric", "pairs": "value"}).assign(
            aspect="observations"
        ),
    ]
    pd.concat(tables, ignore_index=True).to_csv(
        config.output / "metrics.csv", index=False, encoding="utf-8-sig"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.config))


if __name__ == "__main__":
    main()
