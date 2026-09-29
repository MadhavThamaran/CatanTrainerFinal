"""Train the policy/value net on self-play data (PLAN.md Stage 5).

Losses: masked cross-entropy between the policy head and the root visit
distribution (mask = the sample's legal encodable actions), plus BCE between
the value head and the mixed z/rootQ target. AdamW, early stop on val loss.

Usage: python -m net.train --data data/gen0.npz --out checkpoints/gen1.pt
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import torch
import torch.nn.functional as F

from .codec import POLICY_SIZE
from .model import save_checkpoint


class _Data:
    def __init__(self, paths: str | list[str]):
        if isinstance(paths, str):
            paths = [paths]
        Xs, vals, ptrs, idxs, pvals = [], [], [np.zeros(1, np.int64)], [], []
        offset = 0
        for path in paths:
            z = np.load(path)
            assert int(z["policy_size"]) == POLICY_SIZE, "codec changed"
            Xs.append(z["X"])
            vals.append(z["value_target"])
            ptrs.append(z["pol_ptr"][1:] + offset)
            idxs.append(z["pol_idx"])
            pvals.append(z["pol_val"])
            offset += len(z["pol_idx"])
        self.X = torch.from_numpy(np.concatenate(Xs))
        self.value = torch.from_numpy(np.concatenate(vals))
        self.pol_ptr = np.concatenate(ptrs)
        self.pol_idx = np.concatenate(idxs)
        self.pol_val = np.concatenate(pvals)
        self.n = len(self.X)
        # Auxiliary targets (M5 fix #6) and per-sample game ids — both
        # optional for older data files.
        auxs, gids = [], []
        for path in paths:
            z = np.load(path)
            if auxs is not None and "aux_target" in z:
                auxs.append(z["aux_target"])
            else:
                auxs = None
            if gids is not None and "game_id" in z:
                gids.append(z["game_id"])
            else:
                gids = None
        self.aux = (
            torch.from_numpy(np.concatenate(auxs)) if auxs is not None else None
        )
        self.game_id = np.concatenate(gids) if gids is not None else None

    def batch(self, ids: np.ndarray, syms: np.ndarray | None = None) -> tuple:
        """Dense policy target + legality mask for a batch of sample ids.
        `syms` (per-sample symmetry ids) applies dihedral augmentation
        (M5 fix #2): features gathered through FEATURE_SRC, policy indices
        mapped through POLICY_PERM."""
        X = self.X[ids]
        if syms is not None:
            from .symmetry import FEATURE_SRC

            X = torch.gather(X, 1, torch.from_numpy(FEATURE_SRC[syms]))
        target = torch.zeros((len(ids), POLICY_SIZE))
        mask = torch.zeros((len(ids), POLICY_SIZE), dtype=torch.bool)
        if syms is not None:
            from .symmetry import POLICY_PERM
        for row, i in enumerate(ids):
            lo, hi = self.pol_ptr[i], self.pol_ptr[i + 1]
            idx = self.pol_idx[lo:hi].astype(np.int64)
            if syms is not None:
                idx = POLICY_PERM[syms[row]][idx]
            idx = torch.from_numpy(idx)
            target[row, idx] = torch.from_numpy(self.pol_val[lo:hi])
            mask[row, idx] = True
        aux = self.aux[ids] if self.aux is not None else None
        return X, target, mask, self.value[ids], aux


AUX_WEIGHT = 0.5


def _loss(model, X, target, mask, value_t, aux_t):
    if aux_t is not None:
        logits, value, aux = model.forward_with_aux(X)
        aux_loss = F.binary_cross_entropy(aux, aux_t)
    else:
        logits, value = model(X)
        aux_loss = torch.zeros(())
    logits = logits.masked_fill(~mask, -1e9)
    policy_loss = -(target * F.log_softmax(logits, dim=-1)).sum(-1).mean()
    value_loss = F.binary_cross_entropy(value, value_t)
    return policy_loss, value_loss, aux_loss


def _default_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def train(
    data_path: str | list[str],
    out_path: str,
    arch: str = "mlp",
    hidden: int = 384,
    blocks: int = 2,
    d: int = 96,
    rounds: int = 3,
    epochs: int = 12,
    batch_size: int = 256,
    lr: float = 1e-3,
    seed: int = 0,
    init_from: str | None = None,
    augment: bool = True,
    board_blind_value: bool = False,
    device: str | None = None,
) -> dict:
    device = device or _default_device()
    torch.manual_seed(seed)
    if device == "cuda":
        torch.cuda.manual_seed_all(seed)
    data = _Data(data_path)
    rng = np.random.default_rng(seed)
    n_val = min(max(64, data.n // 20), max(1, data.n // 4))
    if data.game_id is not None and len(np.unique(data.game_id)) > 8:
        # Split by GAME: same-game samples are correlated, so a per-sample
        # split leaks into validation and biases early stopping.
        games = np.unique(data.game_id)
        rng.shuffle(games)
        val_games = set()
        count = 0
        per_game = data.n / len(games)
        for g in games:
            val_games.add(int(g))
            count += per_game
            if count >= n_val:
                break
        is_val = np.isin(data.game_id, list(val_games))
        val_ids = np.flatnonzero(is_val)
        train_ids = rng.permutation(np.flatnonzero(~is_val))
    else:
        order = rng.permutation(data.n)
        val_ids, train_ids = order[:n_val], order[n_val:]
    assert len(train_ids) > 0, "dataset too small to split"

    if init_from:
        from .model import load_checkpoint

        model = load_checkpoint(init_from).train()
    else:
        from .model import build_model

        cfg = (
            {"arch": "gnn", "d": d, "rounds": rounds}
            if arch == "gnn"
            else {"arch": "mlp", "hidden": hidden, "blocks": blocks}
        )
        cfg["board_blind_value"] = board_blind_value
        model = build_model(cfg)
    model = model.to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)

    def _to_device(batch):
        X, t, m, v, a = batch
        return (
            X.to(device),
            t.to(device),
            m.to(device),
            v.to(device),
            a.to(device) if a is not None else None,
        )

    def val_loss() -> tuple[float, float]:
        model.eval()
        with torch.no_grad():
            pl, vl, chunks = 0.0, 0.0, 0
            for s in range(0, len(val_ids), 1024):
                X, t, m, v, a = _to_device(data.batch(val_ids[s : s + 1024]))
                p, val, _ = _loss(model, X, t, m, v, a)
                pl += float(p)
                vl += float(val)
                chunks += 1
        model.train()
        return pl / chunks, vl / chunks

    best, best_state, history = float("inf"), None, []
    t0 = time.time()
    for epoch in range(epochs):
        rng.shuffle(train_ids)
        for s in range(0, len(train_ids), batch_size):
            ids = train_ids[s : s + batch_size]
            syms = rng.integers(0, 12, size=len(ids)) if augment else None
            X, t, m, v, a = _to_device(data.batch(ids, syms))
            policy_loss, value_loss, aux_loss = _loss(model, X, t, m, v, a)
            loss = policy_loss + value_loss + AUX_WEIGHT * aux_loss
            opt.zero_grad()
            loss.backward()
            opt.step()
        vp, vv = val_loss()
        history.append((epoch, vp, vv))
        print(
            f"epoch {epoch}: val policy {vp:.4f}, val value {vv:.4f} "
            f"({time.time() - t0:.0f}s)",
            flush=True,
        )
        if vp + vv < best:
            best = vp + vv
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    model = model.cpu()
    save_checkpoint(model, out_path)
    print(f"wrote {out_path} (best val loss {best:.4f}, {data.n} samples)")
    return {"best_val": best, "samples": data.n, "history": history}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--arch", choices=("mlp", "gnn"), default="mlp")
    ap.add_argument("--hidden", type=int, default=384)
    ap.add_argument("--blocks", type=int, default=2)
    ap.add_argument("--d", type=int, default=96, help="gnn embedding width")
    ap.add_argument("--rounds", type=int, default=3, help="gnn message-passing rounds")
    ap.add_argument("--init-from", default=None)
    ap.add_argument("--no-augment", action="store_true")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument(
        "--board-blind-value",
        action="store_true",
        help="value/aux heads read board-blind features (memorization fix)",
    )
    ap.add_argument(
        "--device",
        default=None,
        choices=("cpu", "cuda"),
        help="default: cuda if available, else cpu",
    )
    args = ap.parse_args()
    device = args.device or _default_device()
    print(f"training on device: {device}", flush=True)
    train(
        args.data,
        args.out,
        arch=args.arch,
        hidden=args.hidden,
        blocks=args.blocks,
        d=args.d,
        rounds=args.rounds,
        epochs=args.epochs,
        lr=args.lr,
        init_from=args.init_from,
        augment=not args.no_augment,
        board_blind_value=args.board_blind_value,
        device=device,
    )


if __name__ == "__main__":
    main()
