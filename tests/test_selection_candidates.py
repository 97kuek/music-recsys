import json

import pytest

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.research.selection_candidates import DESCRIPTIONS, bind_candidates
from backend.infrastructure.research.store import atomic_json


@pytest.fixture
def candidates(tmp_path):
    saved = dict(
        source_sha256="frozen", manifest={"phase": "validation"}, features_sha256="features"
    )
    saved.update(pipeline={"c": 60}, conditions=[])
    result = dict(
        status="succeeded", phase="validation", population={"users": 4}, sources=[], artifacts={}
    )
    for group in DESCRIPTIONS:
        for seed in [0] if group == "ease" else [42, 43, 44]:
            name = group if seed == 0 else f"{group}-s{seed}"
            condition = dict(name=name, group=group, seed=seed, sources=["ease"])
            saved["conditions"].append(condition)
            directory = tmp_path / name
            directory.mkdir()
            artifacts = {}
            for artifact in ("scores", "recommendations"):
                path = directory / f"{artifact}.parquet"
                path.write_bytes(b"fixture artifact")
                artifacts[artifact] = dict(path=path.name, sha256=file_sha256(path))
            source = tmp_path / "candidates" / "ease"
            source.mkdir(parents=True, exist_ok=True)
            path = source / "candidates.parquet"
            path.write_bytes(b"fixture candidate")
            config = dict(
                condition=condition,
                signature={k: saved[k] for k in ("source_sha256", "manifest", "features_sha256")},
                pipeline=saved["pipeline"],
                source_candidates=[dict(directory=str(source), sha256=file_sha256(path))],
            )
            atomic_json(directory / "config.json", config)
            document = dict(
                status="succeeded",
                phase="validation",
                population=result["population"],
                condition=condition,
                artifacts=artifacts,
                pool_sha256=f"pool-{seed}",
            )
            atomic_json(directory / "result.json", document)
            result["sources"].append(
                dict(
                    condition=condition,
                    directory=str(directory),
                    result_sha256=file_sha256(directory / "result.json"),
                )
            )
    return tmp_path, saved, result


def test_binds_every_seed_rank_and_score_with_static_descriptions(candidates):
    root, saved, result = candidates
    versions, evidence = bind_candidates(root, saved, result)
    assert set(versions) == set(DESCRIPTIONS)
    assert [len(v["runs"]) for v in versions.values()] == [1, 3, 3, 3]
    assert versions["ease"]["runs"][0]["artifacts"]["recommendations"]["sha256"]
    assert "retrieval/ease" in evidence


@pytest.mark.parametrize("artifact", ["ease/scores.parquet", "candidates/ease/candidates.parquet"])
def test_rejects_modified_artifacts(candidates, artifact):
    root, saved, result = candidates
    (root / artifact).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bind_candidates(root, saved, result)


def test_rejects_missing_seed(candidates):
    root, saved, result = candidates
    saved["conditions"].pop()
    with pytest.raises(ValueError, match="every frozen seed"):
        bind_candidates(root, saved, result)


def test_rejects_mixed_processing_versions(candidates):
    root, saved, result = candidates
    path = root / "ease" / "config.json"
    config = json.loads(path.read_text())
    config["signature"]["source_sha256"] = "old-version"
    atomic_json(path, config)
    with pytest.raises(ValueError, match="processing version"):
        bind_candidates(root, saved, result)


def test_rejects_different_rrf_mmr_pool_even_with_updated_hash(candidates):
    root, saved, result = candidates
    path = root / "ease-bpr-mmr0.8-s42" / "result.json"
    run = json.loads(path.read_text())
    run["pool_sha256"] = "other-pool"
    atomic_json(path, run)
    for ref in result["sources"]:
        if ref["condition"] == run["condition"]:
            ref["result_sha256"] = file_sha256(path)
    with pytest.raises(ValueError, match="pools differ"):
        bind_candidates(root, saved, result)
