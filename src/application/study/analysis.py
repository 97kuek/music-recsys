"""Read consented snapshots through a repository and reuse domain metric calculations."""

from src.domain.study.online_analysis import analyze_online


def analyze_repository(repository, *, now, plans=()):
    payloads, assignments = repository.analysis_snapshot(now)
    return analyze_online(payloads, assignments=assignments, plans=plans, now=now)
