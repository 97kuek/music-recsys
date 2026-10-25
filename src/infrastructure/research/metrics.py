"""Shared saved metric rows and fixed-seed estimates without averaged confidence bounds."""

from statistics import fmean, stdev


def metric_rows(result):
    for row in result["accuracy"]:
        yield dict(aspect="accuracy", **row)
    for row in result["diversity"]:
        for name in ("ild", "catalog_coverage", "novelty"):
            yield dict(
                aspect="diversity",
                task=row["task"],
                k=row["k"],
                metric=name,
                mean=row[name],
                n_evaluated=row.get(name + "_evaluated"),
                n_excluded=row.get(name + "_excluded"),
            )
    names = {"evaluation_seconds", "ranking_seconds", "peak_memory_bytes"} | result[
        "efficiency"
    ].keys()
    for name in sorted(names - {"model"}):
        yield dict(
            aspect="efficiency", task="all", metric=name, mean=result["efficiency"].get(name)
        )
    for row in result.get("observations", []):
        yield dict(aspect="observations", **row)


def _key(row):
    return tuple(
        row.get(k)
        for k in (
            "aspect",
            "task",
            "window_days",
            "day_weight",
            "metric",
            "k",
            "phase",
            "observation",
            "bin",
        )
    )


def average_rows(records, metadata):
    """Average fixed-seed estimates, never average interval endpoints or user counts."""
    mapped = [{_key(r): r for r in metric_rows(record)} for record in records]
    if any(set(m) != set(mapped[0]) for m in mapped):
        raise ValueError("Seed metric keys differ")
    output = []
    for key, first in mapped[0].items():
        rows = [m[key] for m in mapped]
        row = {**metadata, **first, "n_seeds": len(records)}
        if first["aspect"] == "observations":
            if any(r != first for r in rows):
                raise ValueError("Seed observation distributions differ")
        else:
            estimates = [r["mean"] for r in rows]
            valid = all(v is not None for v in estimates)
            row["mean"] = fmean(estimates) if valid else None
            row["seed_sd"] = stdev(estimates) if valid and len(records) > 1 else None
            if len(records) > 1:
                row["ci_low"] = row["ci_high"] = None
            for name in ("n_evaluated", "n_excluded"):
                counts = [r.get(name) for r in rows]
                if first["aspect"] == "accuracy" and len(set(counts)) != 1:
                    raise ValueError("Accuracy populations differ across fixed seeds")
                row[name] = counts[0] if len(set(counts)) == 1 else None
                measured = [n for n in counts if n is not None]
                row[name + "_min"] = min(measured) if measured else None
                row[name + "_max"] = max(measured) if measured else None
        output.append(row)
    return output
