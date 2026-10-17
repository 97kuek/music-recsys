from pathlib import Path


def test_deterministic_model_repeated_seeds(tmp_path):
    from dataclasses import replace

    from src.data import load_dataset
    from src.evaluation.tracking import TrackingConfig
    from src.validation_study import repeat_validation

    data = load_dataset(Path("tests/fixtures/examples/toy/dataset.json"), top_k=2)
    data = replace(data, provenance={"phase": "validation"})
    result = repeat_validation(data, tmp_path, seeds=(0, 42), tracking=TrackingConfig())
    assert result["deterministic"] is True
    assert len(result["runs"]) == 3
    assert [r["seed"] for r in result["runs"]] == [0, 42, 42]
    assert result["runs"][0]["ndcg"] == result["runs"][1]["ndcg"]
    assert len({r["output_sha256"] for r in result["runs"]}) == 1


def test_stability_never_uses_test(tmp_path):
    from dataclasses import replace

    import pytest

    from src.data import load_dataset
    from src.evaluation.tracking import TrackingConfig
    from src.validation_study import repeat_validation

    data = replace(
        load_dataset(Path("tests/fixtures/examples/toy/dataset.json"), top_k=2),
        provenance={"phase": "test"},
    )
    with pytest.raises(ValueError, match="validation"):
        repeat_validation(data, tmp_path, seeds=(42,), tracking=TrackingConfig())


def test_repeated_identical_failures_are_not_a_success(tmp_path, monkeypatch):
    from dataclasses import replace

    from src.data import load_dataset
    from src.evaluation.tracking import TrackingConfig
    from src.recommenders.popularity import PopularityRecommender
    from src.validation_study import repeat_validation

    def fail(self, request):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(PopularityRecommender, "recommend", fail)
    data = replace(
        load_dataset(Path("tests/fixtures/examples/toy/dataset.json"), top_k=2),
        provenance={"phase": "validation"},
    )
    result = repeat_validation(data, tmp_path, seeds=(42,), tracking=TrackingConfig())
    assert result["deterministic"] is True
    assert result["passed"] is False
