"""M5 fixes #6-#7: GNN forward/checkpoint, symmetry equivariance (the
structural property the architecture exists to provide), aux-target
pipeline."""
import random

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from engine import Phase, apply_action, legal_actions, new_game
from net.codec import POLICY_SIZE
from net.encode import FEATURE_DIM, StateEncoder
from net.model import (
    AUX_DIM,
    GraphPolicyValueNet,
    PolicyValueNet,
    load_checkpoint,
    save_checkpoint,
)
from net.symmetry import FEATURE_SRC, NUM_SYMMETRIES, POLICY_PERM


def _gnn():
    torch.manual_seed(0)
    return GraphPolicyValueNet(d=32, rounds=2).eval()


def _encoded_state(seed=17, plies=100):
    state = new_game(seed)
    rng = random.Random(seed)
    for _ in range(plies):
        if state.phase is Phase.GAME_OVER:
            break
        apply_action(state, rng.choice(legal_actions(state)))
    return torch.from_numpy(StateEncoder().encode(state, 0)[None, :])


def test_gnn_forward_shapes_and_aux():
    model = _gnn()
    x = torch.randn(3, FEATURE_DIM)
    logits, value = model(x)
    assert logits.shape == (3, POLICY_SIZE)
    assert value.shape == (3,)
    assert ((value >= 0) & (value <= 1)).all()
    logits2, value2, aux = model.forward_with_aux(x)
    assert torch.allclose(logits, logits2)
    assert aux.shape == (3, AUX_DIM)
    assert ((aux >= 0) & (aux <= 1)).all()


def test_gnn_is_equivariant_to_board_symmetries():
    """encode(T_s(state)) through the GNN must equal the permuted policy and
    the identical value — the inductive bias the MLP lacked, by construction
    (symmetric mean aggregation + shared per-entity weights)."""
    model = _gnn()
    x = _encoded_state()
    with torch.no_grad():
        logits, value = model(x)
        for s in range(NUM_SYMMETRIES):
            xs = x[:, torch.from_numpy(FEATURE_SRC[s])]
            ls, vs = model(xs)
            perm = torch.from_numpy(POLICY_PERM[s])
            assert torch.allclose(ls[0, perm], logits[0], atol=1e-4), f"sym {s}"
            assert torch.allclose(vs, value, atol=1e-5), f"sym {s}"


def test_mlp_aux_head_shapes():
    model = PolicyValueNet(hidden=64, blocks=1)
    x = torch.randn(2, FEATURE_DIM)
    logits, value, aux = model.forward_with_aux(x)
    assert logits.shape == (2, POLICY_SIZE)
    assert aux.shape == (2, AUX_DIM)


def test_gnn_checkpoint_round_trip(tmp_path):
    model = _gnn()
    path = tmp_path / "g.pt"
    save_checkpoint(model, path)
    loaded = load_checkpoint(path)
    assert isinstance(loaded, GraphPolicyValueNet)
    x = torch.randn(2, FEATURE_DIM)
    with torch.no_grad():
        a_l, a_v = model(x)
        b_l, b_v = loaded(x)
    assert torch.allclose(a_l, b_l)
    assert torch.allclose(a_v, b_v)


def test_selfplay_emits_aux_targets_and_training_consumes_them(tmp_path):
    from net.selfplay import pack, selfplay_game
    from net.train import train

    r = selfplay_game((5, 8, 1, None, False, 0.5))
    assert "error" not in r, r
    assert r["aux_target"].shape == (len(r["X"]), AUX_DIM)
    assert ((r["aux_target"] >= 0) & (r["aux_target"] <= 1)).all()
    # my_vp + opp_vp columns: winner-side samples should show 15/16 somewhere
    data_path = str(tmp_path / "aux.npz")
    pack([r], data_path)
    z = np.load(data_path)
    assert "aux_target" in z
    out = train(
        data_path,
        str(tmp_path / "m.pt"),
        arch="gnn",
        d=24,
        rounds=1,
        epochs=2,
        batch_size=32,
    )
    assert out["samples"] == len(r["X"])
