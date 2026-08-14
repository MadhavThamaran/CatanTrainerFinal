"""M5 fixes #1-#3 validation: dihedral symmetry correctness (encode and
legal-action sets must commute with board transforms), relational encoder
dims, clean self-play targets."""
import random

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from engine import ActionType, Phase, apply_action, legal_actions, new_game
from net.codec import encode_action
from net.encode import FEATURE_DIM, StateEncoder
from net.symmetry import (
    EDGE_PERMS,
    FEATURE_SRC,
    HEX_PERMS,
    NUM_SYMMETRIES,
    POLICY_PERM,
    VERTEX_PERMS,
    transform_state,
)


def _mid_game_state(seed=9, plies=120):
    """A random mid-game position with buildings, roads, and a moved robber,
    advanced to a decision point with at least 2 encodable actions."""
    state = new_game(seed)
    rng = random.Random(seed)
    for _ in range(plies):
        if state.phase is Phase.GAME_OVER:
            break
        apply_action(state, rng.choice(legal_actions(state)))
    for _ in range(200):
        actions = legal_actions(state)
        encodable = [a for a in actions if encode_action(a) is not None]
        if len(encodable) >= 2 or state.phase is Phase.GAME_OVER:
            break
        apply_action(state, rng.choice(actions))
    return state


def test_perms_are_bijections_and_identity_first():
    for s in range(NUM_SYMMETRIES):
        assert sorted(HEX_PERMS[s]) == list(range(19))
        assert sorted(VERTEX_PERMS[s]) == list(range(54))
        assert sorted(EDGE_PERMS[s]) == list(range(72))
    assert HEX_PERMS[0] == list(range(19))       # s=0 is the identity
    assert VERTEX_PERMS[0] == list(range(54))
    # 12 distinct symmetries
    assert len({tuple(p) for p in VERTEX_PERMS}) == 12


def test_encode_commutes_with_transform():
    state = _mid_game_state()
    enc = StateEncoder()
    base = enc.encode(state, viewer=0)
    assert base.shape == (FEATURE_DIM,)
    for s in range(NUM_SYMMETRIES):
        transformed = transform_state(state, s)
        direct = StateEncoder().encode(transformed, viewer=0)
        via_perm = base[FEATURE_SRC[s]]
        assert np.allclose(direct, via_perm), f"symmetry {s} feature mismatch"


def test_legal_actions_commute_with_transform():
    state = _mid_game_state(seed=13, plies=90)
    base = {
        encode_action(a) for a in legal_actions(state) if encode_action(a) is not None
    }
    assert base
    for s in range(NUM_SYMMETRIES):
        transformed = transform_state(state, s)
        t_legal = {
            encode_action(a)
            for a in legal_actions(transformed)
            if encode_action(a) is not None
        }
        mapped = {int(POLICY_PERM[s][i]) for i in base}
        assert mapped == t_legal, f"symmetry {s} is not a rules automorphism"


def test_selfplay_produces_clean_pruned_targets():
    from net.selfplay import selfplay_game

    r = selfplay_game((3, 8, 1, None, False, 0.5))  # tiny budget smoke
    assert "error" not in r, r
    assert len(r["X"]) > 10
    assert r["X"].shape[1] == FEATURE_DIM
    for idx, val in zip(r["pol_idx"], r["pol_val"]):
        assert len(idx) == len(val) > 0
        assert abs(val.sum() - 1.0) < 1e-5
    assert len(r["value_target"]) == len(r["X"])


def test_augmented_batch_matches_manual_transform(tmp_path):
    """Training-time augmentation must equal encoding the transformed state."""
    from net.selfplay import pack
    from net.train import _Data

    state = _mid_game_state(seed=21, plies=60)
    enc = StateEncoder()
    actions = [a for a in legal_actions(state) if encode_action(a) is not None]
    assert len(actions) >= 2
    idx = np.array([encode_action(a) for a in actions[:2]], dtype=np.int64)
    val = np.array([0.7, 0.3], dtype=np.float32)
    game = {
        "X": enc.encode(state, 0)[None, :],
        "value_target": np.array([1.0], dtype=np.float32),
        "pol_idx": [idx],
        "pol_val": [val],
    }
    path = str(tmp_path / "one.npz")
    pack([game], path)
    data = _Data(path)
    for s in (0, 3, 7, 11):
        X, target, mask, _, _ = data.batch(np.array([0]), syms=np.array([s]))
        expected = StateEncoder().encode(transform_state(state, s), 0)
        assert np.allclose(X[0].numpy(), expected)
        for a, p in zip(actions[:2], val):
            mapped = int(POLICY_PERM[s][encode_action(a)])
            assert abs(float(target[0, mapped]) - p) < 1e-6
            assert bool(mask[0, mapped])
