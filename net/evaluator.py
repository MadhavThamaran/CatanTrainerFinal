"""NetEvaluator: the bridge between a trained checkpoint and the search.

Fills the two slots the Stage 3 heuristic occupies in the PUCT engine:
policy priors at node expansion and the value at leaves. Returns
`(priors, value_for_actor)`; priors is None when no action is encodable
(discard nodes) — the search falls back to its heuristic prior there but
still uses the net value.

Per-process singleton loading via `get_evaluator(path)` so multiprocessing
workers pay the checkpoint load once.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import torch

from engine import Action, GameState

from .codec import encode_action
from .encode import StateEncoder
from .model import PolicyValueNet, load_checkpoint


class NetEvaluator:
    """`prior_temperature` < 1 sharpens the policy softmax — a first-gen
    net's visit-distribution targets are flat relative to the z-scored
    heuristic priors it must replace, and PUCT strength is highly sensitive
    to prior sharpness at small simulation budgets. `value_blend` mixes the
    static evaluator into the net value (0 = pure net, 1 = pure static)."""

    def __init__(
        self,
        model: PolicyValueNet,
        prior_temperature: float = 1.0,
        value_blend: float = 0.0,
    ):
        self._model = model.eval()
        self._encoder = StateEncoder()
        self._temp = prior_temperature
        self._blend = value_blend

    @torch.no_grad()
    def evaluate(
        self, state: GameState, actions: list[Action], viewer: int
    ) -> tuple[list[float] | None, float]:
        return self.evaluate_batch([state], [actions], [viewer])[0]

    @torch.no_grad()
    def evaluate_batch(
        self,
        states: list[GameState],
        actions_list: list[list[Action]],
        viewers: list[int],
    ) -> list[tuple[list[float] | None, float]]:
        """One forward for many (state, actions) requests. The lockstep
        driver in `search/engine.py` batches the K determinized trees' leaf
        evaluations through here — profiling showed batch-1 dispatch
        overhead dominating search time."""
        xs = np.stack(
            [self._encoder.encode(s, v) for s, v in zip(states, viewers)]
        )
        logits_t, values_t = self._model(torch.from_numpy(xs))
        logits_all = logits_t.numpy()
        out = []
        for row, (state, actions, viewer) in enumerate(
            zip(states, actions_list, viewers)
        ):
            value_actor = float(values_t[row])
            if self._blend > 0.0:
                from search.value import win_prob_p0

                wp0 = win_prob_p0(state)
                static_actor = wp0 if viewer == 0 else 1.0 - wp0
                value_actor = (
                    (1 - self._blend) * value_actor + self._blend * static_actor
                )
            out.append((self._priors(logits_all[row], actions), value_actor))
        return out

    def _priors(self, logits, actions: list[Action]) -> list[float] | None:
        idxs = [encode_action(a) for a in actions]
        if all(i is None for i in idxs):
            return None  # discard node: value only

        # Masked, temperature-sharpened softmax over the legal actions.
        legal = np.array([logits[i] if i is not None else -1e9 for i in idxs])
        legal = legal / self._temp
        legal -= legal.max()
        exps = np.exp(legal)
        return (exps / exps.sum()).tolist()


@lru_cache(maxsize=4)
def get_evaluator(checkpoint_path: str) -> NetEvaluator:
    torch.set_num_threads(1)  # workers must not oversubscribe cores
    return NetEvaluator(load_checkpoint(checkpoint_path))
