"""Policy/value networks (PLAN.md Stage 5).

Two architectures behind one interface:

- `PolicyValueNet` — residual MLP over the flat feature vector (baseline).
- `GraphPolicyValueNet` (M5 fix #7) — message passing over the actual
  hex/vertex/edge board graph. It consumes the SAME flat encoder vector
  (sliced into per-entity features by precomputed index tables), so data,
  evaluator, and symmetry machinery are unchanged. Its policy head is
  structured: road logits come from edge embeddings, settlement/city logits
  from vertex embeddings, robber logits from hex embeddings, scalar-action
  logits from the global node — assembled into the exact ActionCodec layout.
  With symmetric (mean) neighborhood aggregation the network is equivariant
  to the 12 board symmetries by construction (verified by test), which is
  precisely the inductive bias the flat MLP lacked for road/expansion
  context.

Both expose:
    forward(x)          -> (policy_logits, value)          # inference
    forward_with_aux(x) -> (policy_logits, value, aux)     # training
where `aux` (M5 fix #6) predicts [my final VP, opp final VP, LR mine, LR
opp, LA mine, LA opp] as sigmoids — dense outcome supervision that value
learning was measured to be starved of.

Value heads output P(actor wins) in [0, 1] (the encoder is actor-
perspective). Policy masking to legal moves happens in the evaluator/loss.

With `board_blind_value=True` (M5 next lever #1) the value/aux heads read
`_BlindValueFeatures` — occupancy-weighted board interactions plus global
scalars — instead of the trunk/global node, so per-game board identity
cannot reach the outcome-supervised path (the measured memorization channel).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from engine import TOPOLOGY

from .codec import POLICY_SIZE
from . import encode as E

AUX_DIM = 6


# ---------------------------------------------------------------------------
# Board-blind value input (M5 next lever #1)
# ---------------------------------------------------------------------------


class _BlindValueFeatures(nn.Module):
    """Value/aux input that excludes board identity.

    The 5,000-game run showed the value head memorizing outcome-per-board:
    each game's unique terrain/number layout fingerprints its result, and a
    value path that reads raw board planes can exploit that (val value BCE
    diverged 0.45 -> 0.60; dropout only halved it). This module replaces the
    trunk read-out with features where board information enters *only
    multiplied by player occupancy* — production a player actually collects,
    ports a player actually reaches — plus the global scalars. An unbuilt
    hex's identity cannot reach the value head at all.

    Derived from the flat encoder vector by fixed, parameter-free transforms
    so existing self-play data files train it unchanged.
    """

    def __init__(self):
        super().__init__()
        vh_idx, vh_mask = _padded(TOPOLOGY.vertex_hexes, 3)
        self.register_buffer("vert_hex", torch.from_numpy(vh_idx))
        self.register_buffer("vert_hex_mask", torch.from_numpy(vh_mask))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b = x.shape[0]
        terrain = x[:, E.O_TERRAIN : E.O_TERRAIN + 19 * 6].view(b, 19, 6)
        pip = x[:, E.O_PIP : E.O_PIP + 19]
        robber = x[:, E.O_ROBBER : E.O_ROBBER + 19]
        ports = x[:, E.O_PORTS : E.O_PORTS + 54 * 6].view(b, 54, 6)
        bld = x[:, E.O_BUILDINGS : E.O_BUILDINGS + 54 * 4].view(b, 54, 4)
        scalars = x[:, E.O_SCALARS :]

        # Per-hex per-resource pips: encoder terrain columns 0..4 are
        # FOREST/HILLS/PASTURE/FIELDS/MOUNTAINS, aligned with the Resource
        # order (WOOD/BRICK/SHEEP/WHEAT/ORE); column 5 (desert) is dropped.
        hex_res = terrain[:, :, :5] * pip.unsqueeze(-1)             # (B,19,5)
        live = hex_res * (1.0 - robber).unsqueeze(-1)               # robber-adjusted
        mask = self.vert_hex_mask.unsqueeze(0)
        vert_raw = (hex_res[:, self.vert_hex] * mask).sum(dim=2)    # (B,54,5)
        vert_live = (live[:, self.vert_hex] * mask).sum(dim=2)

        my_mult = bld[:, :, 0] + 2.0 * bld[:, :, 1]                 # (B,54)
        opp_mult = bld[:, :, 2] + 2.0 * bld[:, :, 3]
        my_prod = (vert_live * my_mult.unsqueeze(-1)).sum(dim=1) / 4.0
        opp_prod = (vert_live * opp_mult.unsqueeze(-1)).sum(dim=1) / 4.0
        blocked = (vert_raw - vert_live).sum(dim=-1)                # (B,54)
        my_blocked = (blocked * my_mult).sum(dim=1, keepdim=True) / 2.0
        opp_blocked = (blocked * opp_mult).sum(dim=1, keepdim=True) / 2.0

        occ_me = (bld[:, :, 0] + bld[:, :, 1]).unsqueeze(-1)
        occ_opp = (bld[:, :, 2] + bld[:, :, 3]).unsqueeze(-1)
        my_ports = (ports * occ_me).amax(dim=1)                     # (B,6)
        opp_ports = (ports * occ_opp).amax(dim=1)

        return torch.cat(
            [my_prod, opp_prod, my_blocked, opp_blocked, my_ports, opp_ports, scalars],
            dim=-1,
        )


BLIND_DIM = 5 + 5 + 1 + 1 + 6 + 6 + (E.FEATURE_DIM - E.O_SCALARS)


def _blind_value_trunk() -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(BLIND_DIM, 128), nn.ReLU(), nn.Linear(128, 128), nn.ReLU()
    )


# ---------------------------------------------------------------------------
# Baseline residual MLP
# ---------------------------------------------------------------------------


class _Block(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.fc1 = nn.Linear(dim, dim)
        self.fc2 = nn.Linear(dim, dim)
        self.norm = nn.LayerNorm(dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = torch.relu(self.fc1(x))
        return torch.relu(self.norm(x + self.fc2(h)))


class PolicyValueNet(nn.Module):
    arch = "mlp"

    def __init__(
        self,
        hidden: int = 384,
        blocks: int = 2,
        value_dropout: float = 0.3,
        board_blind_value: bool = False,
    ):
        super().__init__()
        self.hidden = hidden
        self.blocks = blocks
        self.value_dropout = value_dropout
        self.board_blind_value = board_blind_value
        self.stem = nn.Sequential(nn.Linear(E.FEATURE_DIM, hidden), nn.ReLU())
        self.trunk = nn.Sequential(*[_Block(hidden) for _ in range(blocks)])
        self.policy = nn.Linear(hidden, POLICY_SIZE)
        # Separate dropout module (no parameters -> old checkpoints' state
        # dicts still load; nn.Sequential indices unchanged). Applied to the
        # value/aux read-out only: the 5,000-game run showed the value head
        # memorizing outcome-per-board (each game's unique board fingerprints
        # its result), while the policy head generalized fine.
        self.val_drop = nn.Dropout(value_dropout)
        if board_blind_value:
            self.blind = _BlindValueFeatures()
            self.value_trunk = _blind_value_trunk()
            read = 128
        else:
            read = hidden
        self.value = nn.Sequential(nn.Linear(read, 96), nn.ReLU(), nn.Linear(96, 1))
        self.aux = nn.Linear(read, AUX_DIM)

    def _trunk(self, x: torch.Tensor) -> torch.Tensor:
        return self.trunk(self.stem(x))

    def _value_read(self, x: torch.Tensor, h: torch.Tensor) -> torch.Tensor:
        if self.board_blind_value:
            return self.val_drop(self.value_trunk(self.blind(x)))
        return self.val_drop(h)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self._trunk(x)
        hv = self._value_read(x, h)
        return self.policy(h), torch.sigmoid(self.value(hv)).squeeze(-1)

    def forward_with_aux(self, x: torch.Tensor):
        h = self._trunk(x)
        hv = self._value_read(x, h)
        return (
            self.policy(h),
            torch.sigmoid(self.value(hv)).squeeze(-1),
            torch.sigmoid(self.aux(hv)),
        )

    def config(self) -> dict:
        return {
            "arch": "mlp",
            "hidden": self.hidden,
            "blocks": self.blocks,
            "value_dropout": self.value_dropout,
            "board_blind_value": self.board_blind_value,
        }


# ---------------------------------------------------------------------------
# Graph network over the board topology (M5 fix #7)
# ---------------------------------------------------------------------------


def _entity_feature_indices():
    """Index tables slicing the flat encoder vector into per-entity blocks."""
    hex_idx = np.zeros((19, 8), dtype=np.int64)
    for h in range(19):
        hex_idx[h, :6] = E.O_TERRAIN + h * 6 + np.arange(6)
        hex_idx[h, 6] = E.O_PIP + h
        hex_idx[h, 7] = E.O_ROBBER + h
    vert_idx = np.zeros((54, 14), dtype=np.int64)
    for v in range(54):
        vert_idx[v, :6] = E.O_PORTS + v * 6 + np.arange(6)
        vert_idx[v, 6] = E.O_PVALUE + v
        vert_idx[v, 7:11] = E.O_BUILDINGS + v * 4 + np.arange(4)
        vert_idx[v, 11:14] = E.O_VFLAGS + v * 3 + np.arange(3)
    edge_idx = np.zeros((72, 2), dtype=np.int64)
    for e in range(72):
        edge_idx[e] = E.O_ROADS + e * 2 + np.arange(2)
    glob_idx = np.arange(E.O_SCALARS, E.FEATURE_DIM, dtype=np.int64)
    return hex_idx, vert_idx, edge_idx, glob_idx


def _padded(adj: list[list[int]], width: int):
    """Pad variable-degree adjacency to (n, width) + mask."""
    n = len(adj)
    idx = np.zeros((n, width), dtype=np.int64)
    mask = np.zeros((n, width, 1), dtype=np.float32)
    for i, nbrs in enumerate(adj):
        for j, k in enumerate(nbrs):
            idx[i, j] = k
            mask[i, j, 0] = 1.0
    return idx, mask


class _Update(nn.Module):
    """Residual node update from concatenated [self, messages...]."""

    def __init__(self, in_dim: int, d: int):
        super().__init__()
        self.mlp = nn.Sequential(nn.Linear(in_dim, d), nn.ReLU(), nn.Linear(d, d))
        self.norm = nn.LayerNorm(d)

    def forward(self, h: torch.Tensor, msg: torch.Tensor) -> torch.Tensor:
        return torch.relu(self.norm(h + self.mlp(msg)))


class GraphPolicyValueNet(nn.Module):
    arch = "gnn"

    def __init__(
        self,
        d: int = 96,
        rounds: int = 3,
        value_dropout: float = 0.3,
        board_blind_value: bool = False,
    ):
        super().__init__()
        self.d = d
        self.rounds = rounds
        self.value_dropout = value_dropout
        self.board_blind_value = board_blind_value

        hex_idx, vert_idx, edge_idx, glob_idx = _entity_feature_indices()
        self.register_buffer("hex_feat", torch.from_numpy(hex_idx))
        self.register_buffer("vert_feat", torch.from_numpy(vert_idx))
        self.register_buffer("edge_feat", torch.from_numpy(edge_idx))
        self.register_buffer("glob_feat", torch.from_numpy(glob_idx))

        self.register_buffer(
            "hex_vert", torch.tensor(TOPOLOGY.hex_vertices, dtype=torch.long)
        )
        vh_idx, vh_mask = _padded(TOPOLOGY.vertex_hexes, 3)
        ve_idx, ve_mask = _padded(TOPOLOGY.vertex_edges, 3)
        self.register_buffer("vert_hex", torch.from_numpy(vh_idx))
        self.register_buffer("vert_hex_mask", torch.from_numpy(vh_mask))
        self.register_buffer("vert_edge", torch.from_numpy(ve_idx))
        self.register_buffer("vert_edge_mask", torch.from_numpy(ve_mask))
        self.register_buffer(
            "edge_vert", torch.tensor(TOPOLOGY.edge_vertices, dtype=torch.long)
        )

        self.embed_hex = nn.Linear(8, d)
        self.embed_vert = nn.Linear(14, d)
        self.embed_edge = nn.Linear(2, d)
        self.embed_glob = nn.Linear(len(glob_idx), d)

        self.upd_hex = nn.ModuleList([_Update(3 * d, d) for _ in range(rounds)])
        self.upd_vert = nn.ModuleList([_Update(4 * d, d) for _ in range(rounds)])
        self.upd_edge = nn.ModuleList([_Update(3 * d, d) for _ in range(rounds)])
        self.upd_glob = nn.ModuleList([_Update(4 * d, d) for _ in range(rounds)])

        # Structured policy heads -> assembled in ActionCodec order:
        # [0,54) v0 | [54,126) e0 | [126,198) e1 | [198,252) v1 |
        # [252,306) v2 | [306,325) hex | [325,370) global scalar actions.
        self.head_vert = nn.Linear(d, 3)   # setup-settle, settle, city
        self.head_edge = nn.Linear(d, 2)   # setup-road, road
        self.head_hex = nn.Linear(d, 1)    # robber
        self.head_glob = nn.Linear(d, POLICY_SIZE - 325)
        self.val_drop = nn.Dropout(value_dropout)  # see PolicyValueNet note
        if board_blind_value:
            self.blind = _BlindValueFeatures()
            self.value_trunk = _blind_value_trunk()
            read = 128
        else:
            read = d
        self.value = nn.Sequential(nn.Linear(read, 96), nn.ReLU(), nn.Linear(96, 1))
        self.aux = nn.Linear(read, AUX_DIM)

    def _masked_mean(self, src, idx, mask):
        # src: (B, N, d); idx: (M, W); mask: (M, W, 1) -> (B, M, d)
        gathered = src[:, idx] * mask
        return gathered.sum(dim=2) / mask.sum(dim=1).clamp(min=1.0)

    def _trunk(self, x: torch.Tensor):
        hx = torch.relu(self.embed_hex(x[:, self.hex_feat]))      # (B,19,d)
        vt = torch.relu(self.embed_vert(x[:, self.vert_feat]))    # (B,54,d)
        eg = torch.relu(self.embed_edge(x[:, self.edge_feat]))    # (B,72,d)
        gl = torch.relu(self.embed_glob(x[:, self.glob_feat]))    # (B,d)

        for r in range(self.rounds):
            g = gl.unsqueeze(1)
            hx_msg = torch.cat(
                [hx, vt[:, self.hex_vert].mean(dim=2), g.expand(-1, 19, -1)], dim=-1
            )
            vt_msg = torch.cat(
                [
                    vt,
                    self._masked_mean(hx, self.vert_hex, self.vert_hex_mask),
                    self._masked_mean(eg, self.vert_edge, self.vert_edge_mask),
                    g.expand(-1, 54, -1),
                ],
                dim=-1,
            )
            eg_msg = torch.cat(
                [eg, vt[:, self.edge_vert].mean(dim=2), g.expand(-1, 72, -1)], dim=-1
            )
            gl_msg = torch.cat(
                [gl, vt.mean(dim=1), hx.mean(dim=1), eg.mean(dim=1)], dim=-1
            )
            hx = self.upd_hex[r](hx, hx_msg)
            vt = self.upd_vert[r](vt, vt_msg)
            eg = self.upd_edge[r](eg, eg_msg)
            gl = self.upd_glob[r](gl, gl_msg)
        return hx, vt, eg, gl

    def _heads(self, hx, vt, eg, gl):
        v_logits = self.head_vert(vt)          # (B,54,3)
        e_logits = self.head_edge(eg)          # (B,72,2)
        return torch.cat(
            [
                v_logits[:, :, 0],             # SETUP_PLACE_SETTLEMENT [0,54)
                e_logits[:, :, 0],             # SETUP_PLACE_ROAD  [54,126)
                e_logits[:, :, 1],             # BUILD_ROAD        [126,198)
                v_logits[:, :, 1],             # BUILD_SETTLEMENT  [198,252)
                v_logits[:, :, 2],             # BUILD_CITY        [252,306)
                self.head_hex(hx).squeeze(-1),  # MOVE_ROBBER      [306,325)
                self.head_glob(gl),            # scalar actions    [325,370)
            ],
            dim=1,
        )

    def _value_read(self, x: torch.Tensor, gl: torch.Tensor) -> torch.Tensor:
        if self.board_blind_value:
            return self.val_drop(self.value_trunk(self.blind(x)))
        return self.val_drop(gl)

    def forward(self, x: torch.Tensor):
        hx, vt, eg, gl = self._trunk(x)
        hv = self._value_read(x, gl)
        return self._heads(hx, vt, eg, gl), torch.sigmoid(self.value(hv)).squeeze(-1)

    def forward_with_aux(self, x: torch.Tensor):
        hx, vt, eg, gl = self._trunk(x)
        hv = self._value_read(x, gl)
        return (
            self._heads(hx, vt, eg, gl),
            torch.sigmoid(self.value(hv)).squeeze(-1),
            torch.sigmoid(self.aux(hv)),
        )

    def config(self) -> dict:
        return {
            "arch": "gnn",
            "d": self.d,
            "rounds": self.rounds,
            "value_dropout": self.value_dropout,
            "board_blind_value": self.board_blind_value,
        }


# ---------------------------------------------------------------------------
# Checkpointing (arch-dispatching)
# ---------------------------------------------------------------------------


def build_model(config: dict) -> nn.Module:
    cfg = dict(config)
    arch = cfg.pop("arch", "mlp")
    if arch == "gnn":
        return GraphPolicyValueNet(**cfg)
    return PolicyValueNet(**cfg)


def save_checkpoint(model: nn.Module, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "config": model.config(),
            "feature_dim": E.FEATURE_DIM,
            "policy_size": POLICY_SIZE,
        },
        path,
    )


def load_checkpoint(path: str | Path) -> nn.Module:
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    assert ckpt["feature_dim"] == E.FEATURE_DIM, "encoder layout changed since training"
    assert ckpt["policy_size"] == POLICY_SIZE, "action codec changed since training"
    model = build_model(ckpt["config"])
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model
