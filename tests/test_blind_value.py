"""Board-blind value path (M5 next lever #1).

The 5,000-game run measured the value head memorizing outcome-per-board
(val value BCE 0.45 -> 0.60 across epochs). With `board_blind_value=True`
the value/aux heads must be a function of occupancy-weighted board
interactions + global scalars only: an unbuilt hex's terrain/number — the
per-game fingerprint — must be invisible to them while the policy head
still sees the full board.
"""
import random

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from engine import Phase, TOPOLOGY, apply_action, legal_actions, new_game
from net import encode as E
from net.encode import StateEncoder
from net.model import (
    GraphPolicyValueNet,
    PolicyValueNet,
    load_checkpoint,
    save_checkpoint,
)
from net.symmetry import FEATURE_SRC, NUM_SYMMETRIES


def _midgame_state(seed=17, plies=100):
    state = new_game(seed)
    rng = random.Random(seed)
    for _ in range(plies):
        if state.phase is Phase.GAME_OVER:
            break
        apply_action(state, rng.choice(legal_actions(state)))
    return state


def _mutate_unbuilt_hex(state, x):
    """Rewrite the board identity (terrain one-hot, pip, placement values) of
    a hex with no adjacent buildings and no robber, directly in the feature
    vector. Returns the mutated copy."""
    h = next(
        h
        for h in range(19)
        if h != state.robber_hex
        and all(v not in state.buildings for v in TOPOLOGY.hex_vertices[h])
    )
    x2 = x.clone()
    base = E.O_TERRAIN + h * 6
    col = int(x2[0, base : base + 6].argmax())
    x2[0, base + col] = 0.0
    x2[0, base + (col + 1) % 6] = 1.0
    x2[0, E.O_PIP + h] = 0.8 if float(x2[0, E.O_PIP + h]) != 0.8 else 0.4
    for v in TOPOLOGY.hex_vertices[h]:
        x2[0, E.O_PVALUE + v] += 0.37
    return x2


@pytest.mark.parametrize(
    "make",
    [
        lambda: PolicyValueNet(hidden=64, blocks=1, board_blind_value=True),
        lambda: GraphPolicyValueNet(d=32, rounds=2, board_blind_value=True),
    ],
    ids=["mlp", "gnn"],
)
def test_value_and_aux_blind_to_unbuilt_board_identity(make):
    torch.manual_seed(0)
    model = make().eval()
    state = _midgame_state()
    x = torch.from_numpy(StateEncoder().encode(state, 0)[None, :])
    x2 = _mutate_unbuilt_hex(state, x)
    with torch.no_grad():
        l1, v1, a1 = model.forward_with_aux(x)
        l2, v2, a2 = model.forward_with_aux(x2)
    assert torch.equal(v1, v2), "value head saw an unbuilt hex's identity"
    assert torch.equal(a1, a2), "aux head saw an unbuilt hex's identity"
    assert not torch.allclose(l1, l2), "sanity: mutation never reached the model"


def test_default_value_head_is_not_blind():
    """The property above is what the flag buys — the stock trunk read-out
    does react to the same mutation (this is the memorization channel)."""
    torch.manual_seed(0)
    model = PolicyValueNet(hidden=64, blocks=1).eval()
    state = _midgame_state()
    x = torch.from_numpy(StateEncoder().encode(state, 0)[None, :])
    x2 = _mutate_unbuilt_hex(state, x)
    with torch.no_grad():
        _, v1 = model(x)
        _, v2 = model(x2)
    assert not torch.equal(v1, v2)


def test_blind_gnn_value_invariant_under_board_symmetries():
    torch.manual_seed(0)
    model = GraphPolicyValueNet(d=32, rounds=2, board_blind_value=True).eval()
    state = _midgame_state()
    x = torch.from_numpy(StateEncoder().encode(state, 0)[None, :])
    with torch.no_grad():
        _, value = model(x)
        for s in range(NUM_SYMMETRIES):
            xs = x[:, torch.from_numpy(FEATURE_SRC[s])]
            _, vs = model(xs)
            assert torch.allclose(vs, value, atol=1e-5), f"sym {s}"


def test_blind_checkpoint_round_trip(tmp_path):
    torch.manual_seed(0)
    model = GraphPolicyValueNet(d=32, rounds=2, board_blind_value=True).eval()
    path = tmp_path / "blind.pt"
    save_checkpoint(model, path)
    loaded = load_checkpoint(path)
    assert loaded.board_blind_value
    x = torch.randn(2, E.FEATURE_DIM)
    with torch.no_grad():
        a_l, a_v = model(x)
        b_l, b_v = loaded(x)
    assert torch.allclose(a_l, b_l)
    assert torch.allclose(a_v, b_v)


def test_training_with_blind_value(tmp_path):
    from net.selfplay import pack, selfplay_game
    from net.train import train

    r = selfplay_game((5, 8, 1, None, False, 0.5))
    assert "error" not in r, r
    data_path = str(tmp_path / "d.npz")
    pack([r], data_path)
    out = train(
        data_path,
        str(tmp_path / "m.pt"),
        arch="gnn",
        d=24,
        rounds=1,
        epochs=2,
        batch_size=32,
        board_blind_value=True,
    )
    assert out["samples"] == len(r["X"])
    assert load_checkpoint(str(tmp_path / "m.pt")).board_blind_value
