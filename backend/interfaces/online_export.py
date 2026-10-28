"""One tabular encoding shared by HTTP and CLI; null is empty, measured zero stays zero."""

import csv
import io
import json

TABLES = (
    "lists",
    "outcomes",
    "assignments",
    "sessions",
    "conditions",
    "paired_differences",
    "order_effects",
)


def flatten(row, prefix=""):
    result = {}
    for key, value in row.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, name))
        else:
            result[name] = (
                json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
            )
    return result


def analysis_csv(analysis, table):
    if table not in TABLES:
        raise ValueError("unknown analysis table")
    rows = [
        flatten(
            {
                "format": analysis["format"],
                "generated_at": analysis["generated_at"],
                "protocol_id": experiment["protocol_id"],
                "plan_sha256": experiment["plan_sha256"],
                "config_sha256": experiment["config_sha256"],
                **row,
            }
        )
        for experiment in analysis["experiments"]
        for row in experiment[table]
    ]
    columns = list(dict.fromkeys(key for row in rows for key in row)) or [
        "format",
        "generated_at",
        "protocol_id",
        "plan_sha256",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        # Spreadsheet imports must not execute identifiers as formulas.
        writer.writerow(
            {
                key: "'" + value
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
                for key, value in row.items()
            }
        )
    return stream.getvalue()
