import pytest

torch = pytest.importorskip("torch")

from backend.infrastructure.recommenders.sequence_checkpoint import save_checkpoint  # noqa: E402


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_partial_checkpoint_is_not_published(tmp_path, monkeypatch, failure):
    path = tmp_path / "model.pt"

    def interrupted_save(value, stream):
        stream.write(b"partial weights")
        assert not path.exists()
        raise failure("interrupted")

    monkeypatch.setattr(torch, "save", interrupted_save)
    with pytest.raises(failure, match="interrupted"):
        save_checkpoint(path, torch.nn.Linear(2, 1), ["a"], 0)
    assert list(tmp_path.iterdir()) == []


def test_checkpoint_preserves_existing_and_concurrently_created_file(tmp_path, monkeypatch):
    path = tmp_path / "model.pt"
    network = torch.nn.Linear(2, 1)
    original_save = torch.save

    def concurrent_save(value, stream):
        original_save(value, stream)
        path.write_bytes(b"other writer")

    monkeypatch.setattr(torch, "save", concurrent_save)
    with pytest.raises(FileExistsError):
        save_checkpoint(path, network, ["a"], 0)
    assert path.read_bytes() == b"other writer"
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(FileExistsError):
        save_checkpoint(path, network, ["a"], 0)
    assert path.read_bytes() == b"other writer"
