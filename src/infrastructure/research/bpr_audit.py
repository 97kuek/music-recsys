"""Read-only provenance and population audits for saved BPR epoch comparisons."""

import ast
import hashlib
import subprocess

from src.infrastructure.datasets.listening_data import read_bundle


def revision_audit(repository, recorded):
    """Verify exact recorded trees, then compare relevant code across package moves."""
    critical = [
        "recommenders/full_bpr.py",
        "recommenders/full_factors.py",
        "recommenders/factors.py",
        "recommenders/gpu_runtime.py",
        "gpu_evaluation.py",
        "gpu_metrics.py",
        "streaming_metrics.py",
        "full_io.py",
        "full_scoring.py",
        "evaluate.py",
        "listening_data.py",
        "configuration.py",
    ]

    class WithoutImports(ast.NodeTransformer):
        def visit_Import(self, node):
            return None

        def visit_ImportFrom(self, node):
            return None

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repository)

    snapshots, references, evidence = {}, {}, []
    for revision, expected in sorted(recorded.items()):
        names = git("ls-tree", "-r", "--name-only", revision, "src").decode().splitlines()
        prefix = "src/music_recsys/" if "src/music_recsys/__init__.py" in names else "src/"
        checksum = hashlib.sha256()
        snapshot = {}
        for name in sorted(n for n in names if n.startswith(prefix) and n.endswith(".py")):
            content = git("show", f"{revision}:{name}")
            relative = name[len(prefix) :]
            checksum.update(relative.encode() + b"\0" + content + b"\0")
            snapshot[relative] = content.decode()
        if checksum.hexdigest() != expected:
            raise ValueError("Recorded source hash does not match its Git tree")
        snapshots[revision] = snapshot
        modules = {}
        for name in critical:
            source = snapshot[name].replace("music_recsys", "src")
            # The package moved up one level; this only changes saved lock provenance.
            if name == "evaluate.py" and prefix == "src/music_recsys/":
                source = source.replace("parents[2]", "parents[1]")
            tree = ast.parse(source)
            imports = sorted(
                ast.dump(node)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
            )
            modules[name] = (ast.dump(WithoutImports().visit(tree)), imports)
        if references and modules != references:
            raise ValueError("BPR training/evaluation implementation differs between revisions")
        references = modules
        evidence.append(dict(revision=revision, source_sha256=expected, verified=True))
    return dict(
        revisions=evidence,
        critical_modules=critical,
        equivalent=True,
        differences="package/import relocation and lock-file metadata path; runner/grid/report changes",
    )


def population_audit(bundle, *, window_days, k):
    import duckdb
    import numpy as np

    manifest, paths = read_bundle(bundle)
    if manifest["phase"] != "validation":
        raise ValueError("Only Validation may be audited for model selection")
    connection = duckdb.connect(config={"threads": 2, "memory_limit": "2GB"})
    try:
        for name, path in paths.items():
            connection.read_parquet(str(path)).create_view(name)
        population = connection.execute(
            "SELECT count(DISTINCT user_id),count(DISTINCT item_id),sum(play_count) FROM learning"
        ).fetchone()
        users, items, events = map(int, population)
        connection.execute(
            "CREATE TEMP TABLE histories AS SELECT user_id,count(*) AS history_count "
            "FROM learning GROUP BY user_id"
        )
        connection.execute(
            "CREATE TEMP TABLE truth AS SELECT user_id,item_id,evaluation_play_count,"
            "evaluation_active_days FROM observations WHERE window_days=? AND learning_play_count=0",
            [window_days],
        )
        connection.execute(
            "CREATE TEMP TABLE truth_counts AS SELECT user_id,count(*) AS positives FROM truth GROUP BY user_id"
        )
        eligible = connection.execute(
            "SELECT t.user_id,t.positives,?-h.history_count AS candidates "
            "FROM truth_counts t JOIN histories h USING(user_id) ORDER BY user_id",
            [items],
        ).df()
        discount = np.cumsum(1 / np.log2(np.arange(2, k + 2)))
        n, candidates = eligible.positives.to_numpy(), eligible.candidates.to_numpy()
        if not len(eligible) or (n > candidates).any() or (candidates < k).any():
            raise ValueError("Unexpected truth/candidate population")
        random_ndcg = n / candidates * discount[-1] / discount[np.minimum(n, k) - 1]
        counts = connection.execute(
            "SELECT avg(evaluation_play_count),median(evaluation_play_count),"
            "quantile_cont(evaluation_play_count,.9),avg(evaluation_active_days),"
            "median(evaluation_active_days),quantile_cont(evaluation_active_days,.9) FROM truth"
        ).fetchone()
        summary = dict(
            users=users,
            items=items,
            training_events=events,
            eligible_users=len(eligible),
            excluded_users=users - len(eligible),
            truth_tracks=int(n.sum()),
            truth_mean=float(n.mean()),
            truth_median=float(np.median(n)),
            truth_p90=float(np.quantile(n, 0.9)),
            truth_max=int(n.max()),
            truth_mean_all_users=float(n.sum() / users),
            candidate_mean=float(candidates.mean()),
            candidate_min=int(candidates.min()),
            candidate_max=int(candidates.max()),
            random_ndcg=float(random_ndcg.mean()),
            random_recall=float((k / candidates).mean()),
            random_kind="analytic expectation, not a sampled run",
            play_count=dict(zip(["mean", "median", "p90"], map(float, counts[:3]), strict=True)),
            active_days=dict(zip(["mean", "median", "p90"], map(float, counts[3:]), strict=True)),
        )
        return connection, manifest, summary
    except BaseException:
        connection.close()
        raise


def audit_ranking(connection, trial, *, items, k):
    """Recompute aggregate NDCG/Recall and inspect every saved New recommendation."""
    import numpy as np

    connection.read_parquet(str(trial.output / "recommendations.parquet")).create_view(
        "recommendations", replace=True
    )
    invalid = connection.execute(
        "SELECT count(*) FROM recommendations r LEFT JOIN histories h USING(user_id) "
        "WHERE r.task='new' AND (h.user_id IS NULL OR r.candidate_count<>?-h.history_count "
        "OR len(r.item_ids)<>least(20,r.candidate_count) "
        "OR len(list_distinct(r.item_ids))<>len(r.item_ids))",
        [items],
    ).fetchone()[0]
    population = connection.execute(
        "SELECT count(*),count(DISTINCT user_id) FROM recommendations WHERE task='new'"
    ).fetchone()
    users = connection.execute("SELECT count(*) FROM histories").fetchone()[0]
    if invalid or population != (users, users):
        raise ValueError("Recommendation population/candidate/uniqueness mismatch")
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW ranked AS SELECT r.user_id,u.item_id,u.position "
        "FROM recommendations r,unnest(r.item_ids) WITH ORDINALITY AS u(item_id,position) "
        "WHERE r.task='new'"
    )
    seen = connection.execute(
        "SELECT count(*) FROM learning l JOIN ranked r USING(user_id,item_id)"
    ).fetchone()[0]
    unknown = connection.execute(
        "SELECT count(*) FROM ranked r ANTI JOIN (SELECT DISTINCT item_id FROM learning) i USING(item_id)"
    ).fetchone()[0]
    if seen or unknown:
        raise ValueError("New recommendations contain history or unknown catalog tracks")
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE idcg AS SELECT range AS n,"
        "sum(1/log2(range+1)) OVER (ORDER BY range) AS ideal FROM range(1,?)",
        [k + 1],
    )
    ndcg, recall = connection.execute(
        "WITH hits AS (SELECT r.user_id,count(*) AS hits,sum(1/log2(r.position+1)) AS dcg "
        "FROM ranked r JOIN truth t USING(user_id,item_id) WHERE r.position<=? GROUP BY r.user_id) "
        "SELECT avg(coalesce(h.dcg,0)/i.ideal),avg(coalesce(h.hits,0)::DOUBLE/t.positives) "
        "FROM truth_counts t LEFT JOIN hits h USING(user_id) JOIN idcg i ON i.n=least(t.positives,?)",
        [k, k],
    ).fetchone()
    if not np.isfinite([ndcg, recall]).all():
        raise ValueError("Recomputed metrics are not finite")
    return dict(
        binary_ndcg=float(ndcg), recall=float(recall), seen=seen, unknown=unknown, users=users
    )


def paired_changes(trials, *, window_days, k_index, resamples=2000, seed=42):
    import duckdb
    import numpy as np
    import pandas as pd

    frames = []
    with duckdb.connect(config={"threads": 2, "memory_limit": "1GB"}) as connection:
        for epoch, paths in sorted(trials.items()):
            frame = connection.execute(
                "SELECT user_id,avg(values[?]) AS score,count(*) AS n "
                "FROM read_parquet(?) WHERE task='discovery' AND metric='binary_ndcg' "
                "AND window_days=? GROUP BY user_id ORDER BY user_id",
                [k_index, paths, window_days],
            ).df()
            if set(frame.n) != {len(paths)}:
                raise ValueError("Paired seed population mismatch")
            frames.append(frame.set_index("user_id").score.rename(epoch))
    scores = pd.concat(frames, axis=1)
    if scores.isna().any().any():
        raise ValueError("Paired user population differs")
    rows = []
    for before, after in zip(scores.columns[:-1], scores.columns[1:], strict=True):
        delta = (scores[after] - scores[before]).to_numpy()
        rng = np.random.default_rng(seed)
        means = []
        for start in range(0, resamples, 32):
            indices = rng.integers(0, len(delta), (min(32, resamples - start), len(delta)))
            means.extend(delta[indices].mean(axis=1))
        low, high = np.quantile(means, [0.025, 0.975])
        rows.append(
            dict(
                before=int(before),
                after=int(after),
                mean=float(delta.mean()),
                ci_low=float(low),
                ci_high=float(high),
                users=len(delta),
                improved_users=int((delta > 0).sum()),
                worsened_users=int((delta < 0).sum()),
                resamples=resamples,
                bootstrap_seed=seed,
            )
        )
    return rows
