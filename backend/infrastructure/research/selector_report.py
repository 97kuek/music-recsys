"""Exact task-level replay; never reuse these aggregates for user-dependent policies."""

from itertools import combinations


def task_target(policy, task):
    if task in {"new", "discovery", "retention_proxy"}:
        return policy.new_target
    if task == "repeat":
        return policy.repeat_target
    raise ValueError("A task-level quality metric must specify new or repeat")


def replay_rows(frame, common, policies):
    output, ild = [], []
    for policy in policies:
        for row in frame.to_dict(orient="records"):
            if row["aspect"] not in {"accuracy", "diversity"}:
                continue
            target = task_target(policy, row["task"])
            if row["condition"] == target:
                output.append(
                    dict(row, condition=policy.name, model=policy.name, source_target=target)
                )
        for row in common.to_dict(orient="records"):
            target = task_target(policy, row["task"])
            if row["pair"] == "ease-bpr" and row["condition"] == target:
                ild.append(dict(row, condition=policy.name, source_target=target))
    return output, ild


def replay_pairs(frame, paired, policies):
    """Transfer paired-user intervals, reversing endpoints when the source order flips."""
    rows = []
    for left, right in combinations(policies, 2):
        for task in ("new", "repeat"):
            a, b = task_target(left, task), task_target(right, task)
            if a == b:
                values = frame[
                    (frame.condition == a)
                    & (frame.aspect == "accuracy")
                    & frame.task.isin(["discovery"] if task == "new" else ["repeat"])
                    & frame.day_weight.fillna(1).eq(1)
                ]
                for row in values.to_dict(orient="records"):
                    valid = row["n_evaluated"] > 0
                    rows.append(
                        dict(
                            model_left=left.name,
                            model_right=right.name,
                            task=task,
                            window_days=row["window_days"],
                            metric="paired_difference_" + row["metric"],
                            k=row["k"],
                            mean=0.0 if valid else None,
                            ci_low=0.0 if valid and row["k"] == 20 else None,
                            ci_high=0.0 if valid and row["k"] == 20 else None,
                            n_evaluated=row["n_evaluated"],
                            n_excluded=row["n_excluded"],
                            source_left=a,
                            source_right=b,
                        )
                    )
                continue
            values = paired[
                (paired.task == task) & (paired.model_left == a) & (paired.model_right == b)
            ]
            reverse = values.empty
            if reverse:
                values = paired[
                    (paired.task == task) & (paired.model_left == b) & (paired.model_right == a)
                ]
            if values.empty:
                raise ValueError("Missing paired-user source comparison")
            for row in values.to_dict(orient="records"):
                if reverse:
                    row.update(mean=-row["mean"], ci_low=-row["ci_high"], ci_high=-row["ci_low"])
                rows.append(
                    dict(
                        row,
                        model_left=left.name,
                        model_right=right.name,
                        source_left=a,
                        source_right=b,
                    )
                )
    return rows
