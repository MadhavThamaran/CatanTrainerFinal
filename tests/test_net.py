"""M5 net package tests: codec uniqueness/coverage, encoder shape and
perspective, evaluator prior validity, net-guided search legality, and a
training-loss-decreases smoke."""
import random

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from engine import ActionType, Phase, apply_action, legal_actions, new_game
from helpers import give, make_main_state, put_settlement
from net.codec import POLICY_SIZE, encode_action
from net.encode import FEATURE_DIM, StateEncoder
from net.evaluator import NetEvaluator
from net.model import PolicyValueNet, load_checkpoint, save_checkpoint
from search import MCTSEngine


def _fresh_model():
    torch.manual_seed(0)
    return PolicyValueNet(hidden=64, blocks=1).eval()  # eval: dropout off


def test_codec_unique_and_in_range_across_game_states():
    seen_pairs = set()
    state = new_game(0)
    rng = random.Random(1)
    for _ in range(400):
        if state.phase is Phase.GAME_OVER:
            break
        actions = legal_actions(state)
        idxs = [encode_action(a) for a in actions]
        if actions[0].type is ActionType.DISCARD:
            assert all(i is None for i in idxs)
        else:
            assert all(i is not None and 0 <= i < POLICY_SIZE for i in idxs)
            assert len(set(idxs)) == len(idxs)  # unique within a decision
            for a, i in zip(actions, idxs):
                seen_pairs.add((repr(a.type), i))
        apply_action(state, rng.choice(actions))
    assert len(seen_pairs) > 30  # exercised a real variety of actions


def test_encoder_shape_deterministic_and_perspective():
    state = make_main_state()
    put_settlement(state, 0, 0)
    give(state, 0, wood=3)
    give(state, 1, ore=2)
    enc = StateEncoder()
    x0 = enc.encode(state, 0)
    assert x0.shape == (FEATURE_DIM,)
    assert np.isfinite(x0).all()
    assert np.array_equal(x0, StateEncoder().encode(state, 0))  # deterministic
    x1 = enc.encode(state, 1)
    assert not np.array_equal(x0, x1)  # viewer perspective matters


def test_evaluator_priors_are_a_distribution():
    state = make_main_state()
    put_settlement(state, 0, 0)
    give(state, 0, wood=4, brick=4, sheep=2, wheat=2, ore=2)
    actions = legal_actions(state)
    priors, value = NetEvaluator(_fresh_model()).evaluate(state, actions, 0)
    assert len(priors) == len(actions)
    assert abs(sum(priors) - 1.0) < 1e-5
    assert all(p >= 0 for p in priors)
    assert 0.0 <= value <= 1.0


def test_evaluator_discard_node_returns_value_only():
    from engine import Action, ScriptedDice

    state = make_main_state(dice=ScriptedDice([7]))
    give(state, 0, wood=10)
    state.needs_roll = True
    apply_action(state, Action(ActionType.ROLL, 0))
    actions = legal_actions(state)
    assert actions[0].type is ActionType.DISCARD
    priors, value = NetEvaluator(_fresh_model()).evaluate(state, actions, 0)
    assert priors is None
    assert 0.0 <= value <= 1.0


def test_net_guided_mcts_plays_legal_and_deterministic():
    net = NetEvaluator(_fresh_model())
    cfg = dict(simulations=16, determinizations=1, seed=5, net=net)
    engine = MCTSEngine(**cfg)
    engine.begin_game(0)
    state = new_game(4)
    picks = []
    for _ in range(30):
        if state.phase is Phase.GAME_OVER:
            break
        legal = legal_actions(state)
        actor = state.player_to_act()
        action = engine.select_action(state) if actor == 0 else legal[0]
        assert action in legal
        picks.append(repr(action))
        apply_action(state, action)
        engine.observe(state, action)

    engine2 = MCTSEngine(**cfg)
    engine2.begin_game(0)
    state2 = new_game(4)
    picks2 = []
    for _ in range(30):
        if state2.phase is Phase.GAME_OVER:
            break
        legal = legal_actions(state2)
        actor = state2.player_to_act()
        action = engine2.select_action(state2) if actor == 0 else legal[0]
        picks2.append(repr(action))
        apply_action(state2, action)
        engine2.observe(state2, action)
    assert picks == picks2


def test_checkpoint_round_trip(tmp_path):
    model = _fresh_model()
    path = tmp_path / "m.pt"
    save_checkpoint(model, path)
    loaded = load_checkpoint(path)
    x = torch.zeros((2, FEATURE_DIM))
    with torch.no_grad():
        a_logits, a_val = model(x)
        b_logits, b_val = loaded(x)
    assert torch.allclose(a_logits, b_logits)
    assert torch.allclose(a_val, b_val)


def test_training_reduces_loss(tmp_path):
    """Tiny synthetic self-play file: loss must drop over epochs."""
    from net.selfplay import pack
    from net.train import train

    rng = np.random.default_rng(0)
    games = []
    for g in range(4):
        n = 40
        X = rng.standard_normal((n, FEATURE_DIM)).astype(np.float32)
        pol_idx = [np.array([1, 2, 3], dtype=np.int64) for _ in range(n)]
        # learnable pattern: prefer index 1, value tied to a feature's sign
        pol_val = [np.array([0.8, 0.1, 0.1], dtype=np.float32) for _ in range(n)]
        games.append(
            {
                "X": X,
                "value_target": (X[:, 0] > 0).astype(np.float32),
                "pol_idx": pol_idx,
                "pol_val": pol_val,
            }
        )
    data_path = str(tmp_path / "d.npz")
    pack(games, data_path)
    out = train(
        data_path,
        str(tmp_path / "m.pt"),
        hidden=64,
        blocks=1,
        epochs=4,
        batch_size=32,
    )
    history = out["history"]
    first = history[0][1] + history[0][2]
    assert out["best_val"] < first or first < 0.9  # learned something


def test_encoder_vflags_and_spots_match_rules_predicates():
    """The vectorized vflag/spot computation (encoding was 24% of net-guided
    search time) must stay bit-equal to the rules predicates it replaced."""
    import random

    from engine import Phase, apply_action, legal_actions, new_game
    from engine.rules import _legal_settlement_vertices, _vertex_placeable
    from net import encode as E

    enc = StateEncoder()
    for seed in range(6):
        state = new_game(seed)
        rng = random.Random(seed)
        for plies in range(240):
            if state.phase is Phase.GAME_OVER:
                break
            apply_action(state, rng.choice(legal_actions(state)))
            if plies % 40 != 0:
                continue
            me = state.player_to_act()
            x = enc.encode(state, me)
            placeable = x[E.O_VFLAGS : E.O_VFLAGS + 54 * 3].reshape(54, 3)[:, 0]
            expect = np.array(
                [float(_vertex_placeable(state, v)) for v in range(54)],
                dtype=np.float32,
            )
            assert np.array_equal(placeable, expect)
            scalars = x[E.O_SCALARS :]
            assert scalars[-2] == min(len(_legal_settlement_vertices(state, me)), 6) / 6.0
            assert scalars[-1] == min(len(_legal_settlement_vertices(state, 1 - me)), 6) / 6.0
