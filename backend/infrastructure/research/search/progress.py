"""Convert persisted trial attempts to typed state and back without changing their schema."""

from backend.domain.experiments import RunStatus
from backend.domain.search import Attempt, TrialProgress


def restore_trial(entry):
    attempts = tuple(
        Attempt(RunStatus(saved["status"]), saved.get("seconds"), saved.get("error_type"))
        for saved in (entry or {}).get("attempts", [])
    )
    if entry and attempts and attempts[-1].status.value != entry["status"]:
        raise ValueError("trial status and latest attempt differ")
    return TrialProgress(attempts)


def trial_document(progress):
    attempts = []
    for attempt in progress.attempts:
        saved = {"status": attempt.status.value}
        if attempt.seconds is not None:
            saved.update(seconds=attempt.seconds, error_type=attempt.error_type)
        elif attempt.error_type is not None:
            saved["error_type"] = attempt.error_type
        attempts.append(saved)
    current = progress.current
    document = dict(status=current.status.value, attempts=attempts)
    if current.seconds is not None:
        document["seconds"] = current.seconds
    if current.error_type is not None:
        document["error_type"] = current.error_type
    return document
