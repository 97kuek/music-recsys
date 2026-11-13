"""Sequence optimization, independent of input loading and recommendation ranking."""

import time


def train_sequence(
    network,
    sequences,
    eligible,
    *,
    n_items,
    epochs,
    batch_size,
    negatives,
    learning_rate,
    probe_batches,
    progress,
):
    import torch

    device = sequences.device
    use_gpu = device.type == "cuda"
    history, measurements = [], {}
    optimizer = torch.optim.Adam(network.parameters(), lr=learning_rate, betas=(0.9, 0.98))
    for epoch in range(1, (1 if probe_batches is not None else epochs) + 1):
        tick, total, targets = time.perf_counter(), 0.0, 0
        order = eligible[torch.randperm(len(eligible), device=device)]
        network.train()
        batch_seconds = []
        for start in range(0, len(order), batch_size):
            batch_tick = time.perf_counter()
            batch = sequences[order[start : start + batch_size]]
            x, y = batch[:, :-1], batch[:, 1:]
            # A user's first item cannot be predicted from padding alone.
            y = y.masked_fill(x == 0, 0)
            sampled = torch.randint(1, n_items + 1, (*y.shape, negatives), device=device)
            optimizer.zero_grad(set_to_none=True)
            loss = network.loss(x, y, sampled)
            if not torch.isfinite(loss):
                raise ValueError("Non-finite sequence training loss")
            loss.backward()
            optimizer.step()
            n = int((y != 0).sum())
            total += loss.detach().item() * n
            targets += n
            if probe_batches is not None:
                if use_gpu:
                    torch.cuda.synchronize()
                batch_seconds.append(time.perf_counter() - batch_tick)
                if len(batch_seconds) == probe_batches:
                    break
        if use_gpu:
            torch.cuda.synchronize()
        row = dict(
            epoch=epoch,
            loss=total / targets,
            targets=targets,
            seconds=time.perf_counter() - tick,
        )
        history.append(row)
        if probe_batches is not None:
            measurements["probe_batch_seconds"] = batch_seconds
            measurements["full_epoch_batches"] = (len(eligible) + batch_size - 1) // batch_size
        label = "Resource probe" if probe_batches is not None else f"Epoch {epoch}/{epochs}"
        progress(f"{label}: loss={row['loss']:.6f}; {row['seconds']:.2f}s")
    return history, measurements
