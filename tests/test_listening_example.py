import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_synthetic_example_is_usable_and_refuses_overwrite(tmp_path):
    from src.domain.evaluation.listening import ListeningConfig
    from src.infrastructure.datasets.listening_data import load_listening_dataset

    create = runpy.run_path(str(ROOT / "tests/fixtures/examples/toy/create_listening.py"))[
        "create_example"
    ]
    output = create(tmp_path / "bundles")
    validation = load_listening_dataset(output / "validation", ListeningConfig())
    test = load_listening_dataset(output / "test", ListeningConfig())
    assert len(validation.train_interactions) == 7
    assert len(test.train_interactions) == 15
    assert len(validation.requests) == 8
    assert validation.provenance["phase"] == "validation"
    assert test.provenance["training_periods"] == ["train", "validation"]
    with pytest.raises(FileExistsError):
        create(output)
