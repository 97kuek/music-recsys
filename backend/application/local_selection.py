"""Run local policies using only the seven-field context, with no evaluator input."""

from time import perf_counter


def select_all(requests, policies):
    for user_id, context in requests:
        for policy in policies:
            started = perf_counter()
            target = policy.choose(context)
            elapsed = perf_counter() - started
            yield dict(
                user_id=user_id,
                task=context.task,
                condition=policy.name,
                target_id=target,
                status="ok",
                selection_seconds=elapsed,
            )
