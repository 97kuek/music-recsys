"""eSASRec-style LiGR encoder and shifted-sequence sampled softmax.

Adapted from blondered/transformer_benchmark LiGR/SwiGLU and RecTools 0.13.0
(Copyright 2025 MTS), Apache-2.0; see licenses/transformer-benchmark.txt.
Modifications: standalone modules, explicit safe padding, selected valid targets,
and train-only catalog scoring; no RecTools/Lightning runtime dependency.
"""

import torch
from torch import nn
from torch.nn import functional as F


class LiGRLayer(nn.Module):
    def __init__(self, factors, heads, dropout, multiplier=4):
        super().__init__()
        self.attention = nn.MultiheadAttention(factors, heads, dropout, batch_first=True)
        self.norm1, self.norm2 = nn.LayerNorm(factors), nn.LayerNorm(factors)
        self.gate1, self.gate2 = nn.Linear(factors, factors), nn.Linear(factors, factors)
        self.up = nn.Linear(factors, factors * multiplier, bias=False)
        self.branch = nn.Linear(factors, factors * multiplier, bias=False)
        self.down = nn.Linear(factors * multiplier, factors, bias=False)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, mask):
        normalized = self.norm1(x)
        attended, _ = self.attention(
            normalized, normalized, normalized, attn_mask=mask, need_weights=False
        )
        x = x + torch.sigmoid(self.gate1(x)) * self.dropout(attended)
        normalized = self.norm2(x)
        hidden = F.silu(self.up(normalized)) * self.branch(normalized)
        return x + torch.sigmoid(self.gate2(x)) * self.dropout(self.down(self.dropout(hidden)))


class SequenceNetwork(nn.Module):
    def __init__(self, n_items, length, factors, heads, blocks, dropout):
        super().__init__()
        self.heads = heads
        self.items = nn.Embedding(n_items + 1, factors, padding_idx=0)
        self.positions = nn.Embedding(length, factors)
        self.item_dropout = nn.Dropout(dropout)
        self.embedding_dropout = nn.Dropout(dropout)
        self.layers = nn.ModuleList([LiGRLayer(factors, heads, dropout) for _ in range(blocks)])

    def encode(self, sequences, table):
        batch, length = sequences.shape
        positions = torch.arange(length - 1, -1, -1, device=sequences.device)
        x = self.embedding_dropout(table[sequences] + self.positions(positions))
        # A padding query can attend only itself, avoiding all-masked NaN rows.
        # Real queries cannot read padding keys or future items.
        causal = torch.ones(length, length, device=x.device, dtype=torch.bool).triu(1)
        mask = causal[None] | (sequences == 0)[:, None, :]
        mask = mask & ~torch.eye(length, device=x.device, dtype=torch.bool)[None]
        mask = mask.repeat_interleave(self.heads, dim=0)
        for layer in self.layers:
            x = layer(x, mask)
        return x.masked_fill((sequences == 0).unsqueeze(-1), 0)

    def loss(self, sequences, targets, negatives):
        table = self.item_dropout(self.items.weight)
        hidden = self.encode(sequences, table)
        valid = targets != 0
        if not valid.any():
            raise ValueError("A training batch must contain a shifted target")
        candidates = torch.cat([targets[valid].unsqueeze(1), negatives[valid]], dim=-1)
        logits = (hidden[valid].unsqueeze(1) * table[candidates]).sum(-1)
        return F.cross_entropy(
            logits, torch.zeros(len(logits), device=logits.device, dtype=torch.long)
        )

    def score(self, sequences):
        hidden = self.encode(sequences, self.items.weight)[:, -1]
        return hidden @ self.items.weight[1:].T
