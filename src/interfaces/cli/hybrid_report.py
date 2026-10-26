"""Publish fixed Hybrid Validation comparisons without selecting a winner."""

from collections import defaultdict
from statistics import fmean, stdev

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.als_report import write_csv
from src.infrastructure.research.comparison import staged_report
from src.infrastructure.research.complementarity import paired_score_differences
from src.infrastructure.research.diversity import common_ild
from src.infrastructure.research.metrics import average_rows
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.store import atomic_json
from src.interfaces.notebook.hybrid_figures import figures
from src.interfaces.notebook.listening_analysis import observation_distribution


def publish(study, results):
    import cupy as cp

    groups = defaultdict(list)
    for result in results:
        verify_artifacts(result["directory"], result["artifacts"])
        groups[result["condition"]["group"]].append(result)
    rows = []
    evidence = {}
    observations = observation_distribution(study.config).to_dict(orient="records")
    for name, runs in groups.items():
        rows.extend(
            average_rows(
                [dict(r, observations=observations) for r in runs], dict(condition=name, model=name)
            )
        )
        evidence[name] = dict(
            scores=[str(r["directory"]) + "/scores.parquet" for r in runs], population=study.counts
        )
    for result in results:
        condition = result["condition"]
        if len(condition["sources"]) == 2 and condition["mmr_lambda"] != 1:
            rrf = next(
                r
                for r in results
                if r["condition"]["sources"] == condition["sources"]
                and r["condition"]["mmr_lambda"] == 1
            )
            if rrf["pool_sha256"] != result["pool_sha256"]:
                raise ValueError("MMR candidate pool differs from its RRF control")
    estimates = []
    paired = paired_score_differences(
        evidence,
        list(study.config.evaluation.ks),
        windows=tuple(study.config.evaluation.window_days),
        xp=cp,
        ci_ks=(20,),
        estimates=estimates,
    )
    lookup = {
        (r["condition"], r["task"], r["window_days"], r["metric"], r["k"]): r for r in estimates
    }
    for row in rows:
        if row["aspect"] != "accuracy" or row.get("day_weight") not in (None, 1):
            continue
        task = "new" if row["task"] == "discovery" else row["task"]
        key = row["condition"], task, row["window_days"], row["metric"], row["k"]
        if key in lookup:
            row.update(ci_low=lookup[key]["ci_low"], ci_high=lookup[key]["ci_high"])
    common = []
    items, _, _ = study.catalog()
    for pair in (("ease", "bpr"), ("bm25", "content")):
        sources = {
            r["condition"]["name"]: dict(
                path=str(r["directory"]) + "/recommendations.parquet", result=r
            )
            for r in results
            if all(s.split("-s")[0] in pair for s in r["condition"]["sources"])
        }
        shared = common_ild(
            sources, study.features(), items, list(study.config.evaluation.ks), xp=cp
        )
        for group in groups:
            matching = [
                r
                for r in shared
                if r["scope"] == "common"
                and r["condition"] in {s["condition"]["name"] for s in groups[group]}
            ]
            if not matching:
                continue
            for task in ("new", "repeat"):
                for k in study.config.evaluation.ks:
                    selected = [r for r in matching if r["task"] == task and r["k"] == k]
                    if len({r["n_evaluated"] for r in selected}) != 1:
                        raise ValueError("Seed common ILD populations differ")
                    values = [r["mean"] for r in selected]
                    valid = all(v is not None for v in values)
                    common.append(
                        dict(
                            pair="-".join(pair),
                            condition=group,
                            task=task,
                            k=k,
                            mean=fmean(values) if valid else None,
                            seed_sd=stdev(values) if valid and len(values) > 1 else None,
                            n_evaluated=selected[0]["n_evaluated"],
                            n_excluded=selected[0]["n_excluded"],
                        )
                    )
    with staged_report(study.output) as temporary:
        write_csv(
            temporary / "metrics.csv",
            rows,
            first=(
                "condition",
                "model",
                "aspect",
                "task",
                "window_days",
                "day_weight",
                "metric",
                "k",
                "mean",
            ),
        )
        write_csv(
            temporary / "paired.csv",
            paired,
            first=("model_left", "model_right", "task", "window_days", "metric", "k", "mean"),
        )
        write_csv(
            temporary / "ild.csv",
            common,
            first=("pair", "condition", "task", "k", "mean", "n_evaluated", "n_excluded"),
        )
        from src.interfaces.cli.hybrid_pool import summarize as pool_summary

        pool = pool_summary(results, cp=cp, population=study.counts["users"])
        write_csv(
            temporary / "pool.csv",
            pool,
            first=("group", "task", "window_days", "scope", "mean"),
        )
        names = figures(temporary, rows, common)
        import pandas as pd

        from src.interfaces.notebook.evaluation_figures import observation_figures
        from src.interfaces.notebook.parameter_figures import save

        names.extend(
            save(fig, temporary, name)
            for name, fig in observation_figures(pd.DataFrame(observations)).items()
        )
        study.check()
        atomic_json(
            temporary / "result.json",
            dict(
                format="music-recsys-hybrid-comparison-v1",
                status="succeeded",
                phase="validation",
                model="hybrid",
                population=study.counts,
                primary=study.signature["settings"]["evaluation"],
                selection="not_selected",
                groups=list(groups),
                sources=[
                    dict(
                        condition=r["condition"],
                        directory=r["directory"],
                        result_sha256=file_sha256(str(r["directory"]) + "/result.json"),
                    )
                    for r in results
                ],
                source_sha256=study.signature["source_sha256"],
                figures=names,
                observations=observations,
                artifacts={
                    n: dict(path=n + ".csv", sha256=file_sha256(temporary / (n + ".csv")))
                    for n in ("metrics", "paired", "ild", "pool")
                },
                definitions=dict(
                    uncertainty="K20 four principal accuracy metrics: paired-user bootstrap after per-user fixed-seed averaging; seed SD separate; no multiple comparison correction",
                    ild="Common valid users within each candidate-source pair, all frozen conditions and seeds; intersections differ across pairs",
                    timing="Source retrieval measures include top-200 diagnostics; final pool uses each top-100; amortized GPU cost is not single-request latency",
                    diagnostics="Per-run scale_diagnostics and pool_recall retain actual candidate size and source budget controls",
                ),
            ),
        )
    return str(study.output)
