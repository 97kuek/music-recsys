import json
import os
import runpy
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)
def test_full_hybrid_validation_pairs_seeds_keeps_sources_and_never_reads_test(
    tmp_path, monkeypatch
):
    from src.application.hybrid import run_study
    from src.infrastructure.datasets.listening_data import file_sha256
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.hybrid import HybridStudy
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.evaluate import run
    from src.interfaces.cli.hybrid_report import publish
    from src.interfaces.notebook.full_analysis import curves

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    sources = {}
    for model in ("ease", "bpr", "bm25", "content"):
        for seed in (42, 43, 44) if model == "bpr" else (0,):
            name = f"{model}-{seed}"
            raw = load_settings("configs/content.json")
            parameters = {
                "ease": dict(
                    regularization=10.0, use_gpu=True, block_size=2, memory_budget_mb=4096
                ),
                "bpr": dict(
                    factors=4,
                    iterations=2,
                    learning_rate=0.01,
                    regularization=0.01,
                    use_gpu=True,
                    memory_budget_mb=4096,
                ),
                "bm25": dict(neighbors=2, k1=1.2, b=0.75, use_gpu=True),
                "content": dict(use_idf=True, aggregation="log_count", use_gpu=True),
            }[model]
            output = tmp_path / "sources" / name
            raw["experiment"].update(
                name=name,
                validation_dataset=str(bundle / "validation"),
                test_dataset=str(tmp_path / "MUST_NOT_READ_TEST"),
                features=str(bundle / "features.json"),
                output=str(output),
            )
            raw["model"] = dict(name=model, parameters=parameters)
            raw["search"] = dict(stage="A", seeds=[seed], max_trials=1)
            path = tmp_path / (name + ".json")
            path.write_text(json.dumps(raw))
            run(
                path,
                **(
                    dict(export_path=output / "model", model_version=name) if model == "bpr" else {}
                ),
            )
            sources[name] = output
    original = {
        str(p): file_sha256(p)
        for source in sources.values()
        for p in source.rglob("*")
        if p.is_file()
    }
    raw = load_settings("configs/hybrid.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(tmp_path / "MUST_NOT_READ_TEST"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "hybrid"),
    )
    for model, source in raw["pipeline"]["sources"].items():
        source["validation"] = str(
            tmp_path / "sources" / ("bpr-{seed}" if model == "bpr" else model + "-0")
        )
    path = tmp_path / "hybrid.json"
    path.write_text(json.dumps(raw))
    config = load_study_config(path)
    study = HybridStudy(config, report=publish)
    study.freeze()
    original_prepare = study.prepare

    def prepare(name):
        study.build_source(name)
        return original_prepare(name)

    monkeypatch.setattr(study, "prepare", prepare)
    output = Path(run_study(tuple(study.sources), study.conditions, study))
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert result["selection"] == "not_selected" and len(result["groups"]) == 10
    assert len(result["sources"]) == 18
    assert result["phase"] == "validation"
    for source in result["sources"]:
        directory = Path(source["directory"])
        r = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        assert r["population"]["users"] == 4
        assert r["primary"]["ks"] == list(range(2, 21))
        assert {x["task"] for x in r["diversity"]} == {"new", "repeat"}
        assert r["efficiency"]["api_cost"] is None
        assert (
            study.evaluate(
                next(c for c in study.conditions if c.name == r["condition"]["name"]), {}
            )["condition"]
            == r["condition"]
        )
    assert original == {p: file_sha256(p) for p in original}
    assert not (tmp_path / "MUST_NOT_READ_TEST").exists()
    frame = curves(config)
    assert frame.model.nunique() == 10 and set(frame.task) >= {"discovery", "repeat"}
    assert all((output / name).exists() for name in result["figures"])
    with pytest.raises(FileExistsError):
        study.freeze()


def test_fixed_hybrid_plan_uses_validation_versions_and_exactly_ten_groups(tmp_path):
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.hybrid import plan
    from src.infrastructure.research.search import load_study_config

    raw = load_settings("configs/hybrid.json")
    expected = {
        "ease": dict(regularization=500.0),
        "bpr": dict(factors=128, iterations=500),
        "bm25": dict(neighbors=20, k1=1.2, b=0.75),
        "content": dict(use_idf=True, aggregation="log_count"),
    }
    for model, source in raw["pipeline"]["sources"].items():
        assert "test" not in Path(source["validation"]).parts
        source["validation"] = str(tmp_path / ("bpr-{seed}" if model == "bpr" else model))
        for seed in (42, 43, 44) if model == "bpr" else (0,):
            directory = Path(source["validation"].format(seed=seed))
            directory.mkdir()
            (directory / "config.json").write_text(
                json.dumps(dict(settings=dict(parameters=expected[model]))), encoding="utf-8"
            )
            (directory / "result.json").write_text(
                json.dumps(dict(phase="validation", status="succeeded", model=model)),
                encoding="utf-8",
            )
    path = tmp_path / "hybrid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    config = load_study_config(path)
    spec, retrieval, sources, conditions = plan(config)
    assert spec.top_m == 100 and retrieval == 200 and spec.c == 60
    assert len(sources) == 6 and len(conditions) == 18 and len({c.group for c in conditions}) == 10
    assert all("test" not in Path(s["directory"]).parts for s in sources.values())
    assert all(s["parameters"] == expected[s["model"]] for s in sources.values())
    assert config.metric is config.task is None
    assert len([c for c in conditions if c.mmr_lambda < 1]) == 8
    assert {c.seed for c in conditions if "bpr" in c.group} == {42, 43, 44}
