"""Policy/value network (PLAN.md Stage 5 / M5).

The net fills the two slots the Stage 3 heuristic occupies in the PUCT
engine — policy priors and leaf value. At current data scale the measured
best configuration is an ENSEMBLE (net mixed with the heuristic prior and
static value), which `net_engine()` builds with the validated calibration:

    from net import net_engine
    engine = net_engine("checkpoints/gen2.pt", seed=0)

Pipeline: `python -m net.selfplay` -> `python -m net.train` ->
`python -m examples.net_eval`.
"""
from .codec import POLICY_SIZE, encode_action
from .encode import FEATURE_DIM, StateEncoder


def net_engine(
    checkpoint: str,
    prior_temperature: float = 0.5,
    value_blend: float = 0.5,
    net_prior_mix: float = 0.3,
    **engine_kwargs,
):
    """MCTSEngine with the measured-best net-ensemble calibration.

    Defaults come from the M5 calibration sweeps: sharpened net priors
    (T=0.5) mixed 30/70 with the heuristic prior, net value blended 50/50
    with the static evaluator. Override freely for experiments.
    """
    from search import MCTSEngine

    from .evaluator import NetEvaluator
    from .model import load_checkpoint

    net = NetEvaluator(
        load_checkpoint(checkpoint),
        prior_temperature=prior_temperature,
        value_blend=value_blend,
    )
    return MCTSEngine(net=net, net_prior_mix=net_prior_mix, **engine_kwargs)


__all__ = [
    "FEATURE_DIM",
    "POLICY_SIZE",
    "StateEncoder",
    "encode_action",
    "net_engine",
]
