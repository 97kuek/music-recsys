"""Bind the four selector IDs to verified, immutable Validation artifacts."""

from pathlib import Path

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.research.hybrid_config import read, reference
from backend.infrastructure.research.results import verify_artifacts

DESCRIPTIONS = {
    "ease": "EASE linear collaborative filtering, without rank fusion or MMR",
    "ease-bpr-rrf": "Equal-weight reciprocal rank fusion of EASE and BPR, c=60",
    "ease-bpr-mmr0.8": "EASE+BPR rank fusion followed by tag-based MMR, lambda=0.8",
    "ease-bpr-mmr0.5": "EASE+BPR rank fusion followed by tag-based MMR, lambda=0.5",
}


def bind_candidates(root, saved, result):
    """Return local provenance separately from static descriptions sent to selectors."""
    root = Path(root).resolve()
    if result["status"] != "succeeded" or result["phase"] != "validation":
        raise ValueError("Completed Validation candidates required")
    verify_artifacts(root, result["artifacts"])
    evidence = {}
    versions = {}
    pools = {}
    for group, description in DESCRIPTIONS.items():
        conditions = [c for c in saved["conditions"] if c["group"] == group]
        seeds = [c["seed"] for c in conditions]
        if sorted(seeds) != ([0] if group == "ease" else [42, 43, 44]):
            raise ValueError("Selector candidates require every frozen seed exactly once")
        runs = []
        for condition in conditions:
            directory = (root / condition["name"]).resolve()
            if directory.parent != root:
                raise ValueError("Candidate run must stay inside Validation")
            matches = [s for s in result["sources"] if s["condition"] == condition]
            if len(matches) != 1 or Path(matches[0]["directory"]).resolve() != directory:
                raise ValueError("Candidate run is not in the completed comparison")
            refs = reference(directory)
            if refs["result"]["sha256"] != matches[0]["result_sha256"]:
                raise ValueError("Candidate result changed")
            run, config = read(directory / "result.json"), read(directory / "config.json")
            if (
                run["status"] != "succeeded"
                or run["phase"] != "validation"
                or run["condition"] != condition
                or config["condition"] != condition
                or run["population"] != result["population"]
                or config["pipeline"] != saved["pipeline"]
                or any(
                    config["signature"][key] != saved[key]
                    for key in ("source_sha256", "manifest", "features_sha256")
                )
            ):
                raise ValueError("Candidate condition, population or processing version differs")
            verify_artifacts(directory, run["artifacts"])
            for name, artifact in run["artifacts"].items():
                refs[name] = dict(path=str(directory / artifact["path"]), sha256=artifact["sha256"])
            if not {"recommendations", "scores"} <= refs.keys():
                raise ValueError("Candidate rankings and individual scores required")
            for name, ref in refs.items():
                evidence[f"{condition['name']}/{name}"] = ref
            expected_sources = [root / "candidates" / name for name in condition["sources"]]
            if [
                Path(r["directory"]).resolve() for r in config["source_candidates"]
            ] != expected_sources:
                raise ValueError("Candidate retrieval sources differ")
            for source, ref in zip(expected_sources, config["source_candidates"], strict=True):
                candidate = source / "candidates.parquet"
                if file_sha256(candidate) != ref["sha256"]:
                    raise ValueError("Retrieval candidate artifact changed")
                evidence[f"retrieval/{source.name}"] = dict(
                    path=str(candidate), sha256=ref["sha256"]
                )
            if group != "ease":
                key = condition["seed"]
                if key in pools and pools[key] != run["pool_sha256"]:
                    raise ValueError("RRF/MMR candidate pools differ")
                pools[key] = run["pool_sha256"]
            runs.append(dict(condition=condition, artifacts=refs, pool_sha256=run["pool_sha256"]))
        versions[group] = dict(description=description, runs=runs)
    return versions, evidence
