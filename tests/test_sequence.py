import json
import os
import runpy
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from backend.infrastructure.datasets.listening_data import file_sha256  # noqa: E402
from backend.infrastructure.recommenders.full_sequence import FullSequence  # noqa: E402
from backend.infrastructure.recommenders.sequence_input import suffix_sequences  # noqa: E402
from backend.infrastructure.recommenders.sequence_network import SequenceNetwork  # noqa: E402


def test_causal_padding_and_gradient_are_finite():
    torch.manual_seed(1)
    net = SequenceNetwork(8, 5, 8, 2, 2, 0).eval()
    x = torch.tensor([[0, 0, 1, 2, 3], [0, 0, 0, 0, 0]])
    before = net.encode(x, net.items.weight)
    changed = x.clone()
    changed[0, -1] = 8
    after = net.encode(changed, net.items.weight)
    torch.testing.assert_close(before[:, :-1], after[:, :-1])
    torch.testing.assert_close(net.score(x)[1], torch.zeros(8))
    y = torch.tensor([[0, 0, 2, 3, 4], [0, 0, 0, 0, 0]])
    negatives = torch.ones(2, 5, 3, dtype=torch.long)
    loss = net.loss(x, y, negatives)
    loss.backward()
    assert torch.isfinite(loss) and all(torch.isfinite(p.grad).all() for p in net.parameters())
    # Changing the padding embedding cannot affect real queries, even over multiple blocks.
    original = net.score(x).detach()
    with torch.no_grad():
        net.items.weight[0] = 100
    torch.testing.assert_close(original, net.score(x))


def test_sampled_softmax_matches_full_catalog_when_enumerated():
    torch.manual_seed(2)
    net = SequenceNetwork(4, 2, 8, 2, 1, 0).eval()
    x, y = torch.tensor([[0, 1], [2, 3]]), torch.tensor([[0, 2], [3, 4]])
    negatives = torch.tensor([[[1, 2, 3], [1, 3, 4]], [[1, 2, 4], [1, 2, 3]]])
    actual = net.loss(x, y, negatives)
    hidden = net.encode(x, net.items.weight)[y != 0]
    logits = hidden @ net.items.weight[1:].T
    expected = torch.nn.functional.cross_entropy(logits, y[y != 0] - 1)
    torch.testing.assert_close(actual, expected)
    with pytest.raises(ValueError, match="shifted target"):
        net.loss(x, torch.zeros_like(y), negatives)


def test_ligr_layer_matches_independent_attention_and_gate_equations():
    from backend.infrastructure.recommenders.sequence_network import LiGRLayer

    torch.manual_seed(7)
    layer = LiGRLayer(4, 2, 0).double().eval()
    x = torch.randn(2, 3, 4, dtype=torch.float64)
    mask = torch.ones(3, 3, dtype=torch.bool).triu(1)
    f = torch.nn.functional
    z = f.layer_norm(x, (4,), layer.norm1.weight, layer.norm1.bias, layer.norm1.eps)
    qkv = f.linear(z, layer.attention.in_proj_weight, layer.attention.in_proj_bias)
    q, k, v = [t.reshape(2, 3, 2, 2).transpose(1, 2) for t in qkv.chunk(3, -1)]
    weights = (q @ k.transpose(-1, -2) / np.sqrt(2)).masked_fill(mask, -torch.inf).softmax(-1)
    attention = (weights @ v).transpose(1, 2).reshape(2, 3, 4)
    attention = f.linear(attention, layer.attention.out_proj.weight, layer.attention.out_proj.bias)
    residual = x + torch.sigmoid(f.linear(x, layer.gate1.weight, layer.gate1.bias)) * attention
    z = f.layer_norm(residual, (4,), layer.norm2.weight, layer.norm2.bias, layer.norm2.eps)
    ff = f.linear(
        f.silu(f.linear(z, layer.up.weight)) * f.linear(z, layer.branch.weight), layer.down.weight
    )
    expected = (
        residual + torch.sigmoid(f.linear(residual, layer.gate2.weight, layer.gate2.bias)) * ff
    )
    torch.testing.assert_close(layer(x, mask), expected, atol=1e-12, rtol=1e-12)


def test_suffix_stream_boundaries_and_training_target(monkeypatch):
    from backend.infrastructure.recommenders import sequence_input

    users = np.array([0, 0, 0, 0, 0, 1, 1])
    items = np.array([0, 1, 2, 3, 4, 1, 3])

    def batches(*args, **kwargs):
        for start in range(0, len(users), 2):
            yield users[start : start + 2], items[start : start + 2], np.zeros(2)

    monkeypatch.setattr(sequence_input, "event_batches", batches)
    seq, stats = suffix_sequences([], {"a": 0, "b": 1, "c": 2}, {}, "128MB", 2)
    np.testing.assert_array_equal(seq, [[3, 4, 5], [0, 2, 4], [0, 0, 0]])
    assert stats == dict(
        ordered_events=7,
        truncated_users=1,
        training_users=2,
        users_without_ordered_events=1,
        users_without_shifted_targets=1,
    )


def fixture(tmp_path):
    return runpy.run_path(str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py"))[
        "create_example"
    ](tmp_path / "bundle")


@pytest.mark.parametrize("sequence_events", ["all", "first_listen"])
def test_cli_future_isolation_checkpoint_and_all_users(tmp_path, sequence_events):
    import pyarrow.parquet as pq

    from backend.infrastructure.research.configuration import load_settings
    from backend.interfaces.cli.evaluate import run

    bundle = fixture(tmp_path)
    raw = load_settings("configs/esasrec/all.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "result"),
    )
    raw["model"]["parameters"].update(
        use_gpu=False,
        input_memory_mb=128,
        sequence_length=3,
        factors=8,
        heads=2,
        blocks=1,
        dropout=0,
        negatives=3,
        batch_size=8,
        epochs=2,
        sequence_events=sequence_events,
    )
    config = tmp_path / "config.json"
    config.write_text(json.dumps(raw))
    output = run(config)
    saved = json.loads((output / "config.json").read_text())
    result = json.loads((output / "result.json").read_text())
    assert result["status"] == "succeeded"
    assert result["artifacts"]["model"]["sha256"] == file_sha256(output / "model.pt")
    assert len(saved["execution"]["epochs"]) == 2
    assert (
        len(pq.read_table(output / "recommendations.parquet")) == 2 * result["population"]["users"]
    )
    checkpoint = torch.load(output / "model.pt", weights_only=True)
    assert len(checkpoint["items"]) == result["population"]["items"]
    (bundle / "source/test.parquet").unlink()
    run(config, phase="test")
    with pytest.raises(FileExistsError):
        run(config)
    from backend.interfaces.cli.sequence_probe import run as probe

    profile = probe(config, tmp_path / "probe", batches=1)
    measured = json.loads((profile / "result.json").read_text())
    assert measured["accuracy"] == "not_measured"
    assert measured["execution"]["mode"] == "resource_probe"
    assert len(measured["execution"]["probe_batch_seconds"]) == 1
    assert not (profile / "model.pt").exists()
    assert not (profile / "recommendations.parquet").exists()


def test_future_event_rejected_before_training(tmp_path):
    from datetime import datetime

    import pyarrow as pa
    import pyarrow.parquet as pq

    learning, events = tmp_path / "learning.parquet", tmp_path / "events.parquet"
    pq.write_table(pa.Table.from_pylist([dict(user_id="u", item_id="a", play_count=1)]), learning)
    pq.write_table(
        pa.Table.from_pylist([dict(user_id="u", item_id="a", event_time=datetime(2020, 1, 1))]),
        events,
    )
    with pytest.raises(ValueError, match="future"):
        FullSequence(
            learning,
            events_path=[str(events)],
            events_sha256=[file_sha256(events)],
            prediction_at="2020-01-01",
            use_gpu=False,
            input_memory_mb=128,
            epochs=1,
        )


def test_cuda_request_never_falls_back_to_cpu(tmp_path, monkeypatch):
    from backend.infrastructure.recommenders import gpu_runtime

    monkeypatch.setattr(gpu_runtime, "require_idle_gpu", lambda: "test idle")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="no CPU fallback"):
        FullSequence(
            tmp_path / "missing",
            events_path=["missing"],
            events_sha256=["bad"],
            prediction_at="2020-01-01",
            use_gpu=True,
        )


@pytest.mark.skipif(os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit CUDA test")
def test_network_cpu_cuda_score_and_gradient():
    if not torch.cuda.is_available():
        pytest.fail("CUDA PyTorch required for explicit GPU test")
    torch.manual_seed(3)
    net = SequenceNetwork(5, 3, 8, 2, 2, 0).eval()
    import copy

    cuda = copy.deepcopy(net).cuda()
    x = torch.tensor([[0, 1, 2], [1, 3, 4]])
    torch.testing.assert_close(net.score(x), cuda.score(x.cuda()).cpu(), atol=1e-5, rtol=1e-5)
    y = torch.tensor([[0, 2, 3], [3, 4, 5]])
    negatives = torch.ones(2, 3, 2, dtype=torch.long)
    cpu_loss = net.loss(x, y, negatives)
    gpu_loss = cuda.loss(x.cuda(), y.cuda(), negatives.cuda())
    cpu_loss.backward()
    gpu_loss.backward()
    for a, b in zip(net.parameters(), cuda.parameters(), strict=True):
        torch.testing.assert_close(a.grad, b.grad.cpu(), atol=1e-5, rtol=1e-5)
