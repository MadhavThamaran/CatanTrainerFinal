"""scripts/ladder_dial_probe.py — the exploratory harness behind the v2 ladder table
(LADDER_SPEC §8). The games are stood in for; what is checked is what the saved
results depend on: the config grammar, the rating math (and that the committed
results still follow from it), the seed range, and that `--resume` never reuses a
result measured under different conditions."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

from agents import EpsilonAgent, HeuristicAgent
from search import MCTSEngine
from trainer import ladder

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "ladder_dial_probe.py"
_SAVED = _ROOT / "data" / "ladder_dial_probe_results.json"

# The 2026-10-07 run's configs, in the order that fixed their seeds.
_RUN_CONFIGS = ["gen7:64x3:e0", "gen7:32x2:e0", "gen7:16x2:e0", "gen7:8x1:e0", "gen7:32x2:e0.1",
                "gen7:32x2:e0.2", "gen7:32x2:e0.3", "gen7:32x2:e0.45", "gen7:32x2:e0.65",
                "gen7:32x2:e0.85"]


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("ladder_dial_probe", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _InlinePool:
    """mp.Pool stand-in that runs the jobs here, so the patched play_one is used."""

    def __init__(self, workers):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def imap_unordered(self, fn, jobs):
        return map(fn, jobs)


def _run(probe, monkeypatch, tmp_path, *flags, configs=_RUN_CONFIGS):
    """Run main() with a stand-in for the games; returns the jobs that were played."""
    played = []

    def fake_game(job):
        played.append(job)
        return job[0] % 3 == 0

    monkeypatch.setattr(probe, "play_one", fake_game)
    monkeypatch.setattr(probe.mp, "Pool", _InlinePool)
    monkeypatch.setattr(sys, "argv", ["ladder_dial_probe.py", "--out", str(tmp_path / "out.json"),
                                      "--configs", *configs, *flags])
    probe.main()
    return played


def test_a_config_names_the_net_the_search_and_the_random_move_rate(probe):
    assert probe.parse_config("gen7:16x2:e0.3") == ("gen7", 16, 2, 0.3)
    assert probe.parse_config("gen5:64x3:e0") == ("gen5", 64, 3, 0.0)
    for cfg in _RUN_CONFIGS:
        probe.parse_config(cfg)


@pytest.mark.parametrize("bad", ["", "gen7", "gen7:16:e0.3", "gen7:16x2", "gen7:16x2:0.3", "gen7:axb:e0",
                                 "gen7:16x2:eX", "a:16x2:e0:extra"])
def test_a_malformed_config_is_rejected_with_the_grammar(probe, bad):
    with pytest.raises(ValueError, match="expected <net>:<sims>x<dets>:e<epsilon>"):
        probe.parse_config(bad)


def test_make_builds_the_references_and_wraps_only_noisy_configs(probe, monkeypatch):
    assert isinstance(probe.make("heuristic", 1), HeuristicAgent)
    anchor = probe.make("anchor", 1)                       # ladder.ANCHOR: raw MCTS 160 x 4, no net
    assert isinstance(anchor, MCTSEngine) and anchor.net is None
    assert (anchor.simulations, anchor.determinizations) == (160, 4)

    sentinel = object()
    monkeypatch.setattr("net.evaluator.get_evaluator", lambda path: sentinel)
    noisy = probe.make("gen7:8x1:e0.3", 5)
    assert isinstance(noisy, EpsilonAgent) and noisy.epsilon == 0.3
    assert (noisy.base.simulations, noisy.base.determinizations, noisy.base.net) == (8, 1, sentinel)
    plain = probe.make("gen7:8x1:e0", 5)                   # a rate of 0 is the bare engine
    assert isinstance(plain, MCTSEngine) and plain.net is sentinel


def test_the_elo_scale_is_logistic_and_floored(probe):
    assert probe.to_elo(0.5, 1149.4) == pytest.approx(1149.4)
    assert probe.to_elo(10 / 11, 1000.0) == pytest.approx(1400.0)          # 10:1 odds = 400 Elo
    assert probe.to_elo(0.7, 1000.0) - 1000.0 == pytest.approx(1000.0 - probe.to_elo(0.3, 1000.0))
    floor = 1149.4 + 400 * math.log10(0.005 / 0.995)
    assert probe.to_elo(0.0, 1149.4) == pytest.approx(floor) == pytest.approx(229.9, abs=0.1)
    assert probe.to_elo(1.0, 1149.4) == pytest.approx(1149.4 - (floor - 1149.4))   # the mirror image
    assert probe.REF_ELO["anchor"] == ladder.ANCHOR_ELO


def test_wilson_matches_known_intervals(probe):
    lo, hi = probe.wilson(54, 80)                                          # the 64 x 3 config's result
    assert (lo, hi) == pytest.approx((0.5664, 0.7676), abs=1e-3)
    lo, hi = probe.wilson(40, 80)
    assert lo + hi == pytest.approx(1.0)                                   # symmetric about one half


def test_a_shutout_has_a_finite_upper_bound(probe):
    lo, hi = probe.wilson(0, 80)
    assert lo == 0.0 and hi == pytest.approx(0.0458, abs=1e-3)             # z^2 / (n + z^2)
    assert probe.wilson(80, 80)[1] == 1.0
    # ...so 0 wins in 80 only says the config is below ~620 against the heuristic: the point
    # estimate is the floor, the interval's upper edge is the information.
    assert probe.to_elo(hi, 1149.4) == pytest.approx(622.0, abs=0.5)


def test_the_committed_results_follow_from_the_scripts_own_math(probe):
    saved = json.loads(_SAVED.read_text())
    assert set(_RUN_CONFIGS) <= set(saved)                 # (a later probe may append configs)
    for cfg, r in saved.items():
        ref = probe.REF_ELO[r["ref"]]
        lo, hi = probe.wilson(r["wins"], r["games"])
        assert (r["elo"], r["elo_lo"], r["elo_hi"]) == (
            round(probe.to_elo(r["wins"] / r["games"], ref), 1),
            round(probe.to_elo(lo, ref), 1),
            round(probe.to_elo(hi, ref), 1)), cfg


def test_a_default_run_stays_inside_the_ledger_range(probe, monkeypatch, tmp_path):
    seeds = [seed for seed, _, _ in _run(probe, monkeypatch, tmp_path)]
    assert len(seeds) == 10 * 80
    assert len(set(seeds)) == len(seeds)                   # no seed shared by two games
    assert (min(seeds), max(seeds)) == (93_000_000, 93_009_079)


def test_resume_reuses_only_results_measured_under_the_same_conditions(probe, monkeypatch, tmp_path):
    configs = _RUN_CONFIGS[:3]
    first = _run(probe, monkeypatch, tmp_path, "--games", "10", configs=configs)
    assert len(first) == 30
    assert _run(probe, monkeypatch, tmp_path, "--games", "10", "--resume", configs=configs) == []

    out = tmp_path / "out.json"
    saved = json.loads(out.read_text())
    del saved[configs[1]]
    out.write_text(json.dumps(saved))
    again = _run(probe, monkeypatch, tmp_path, "--games", "10", "--resume", configs=configs)
    assert again == [job for job in first if job[1] == configs[1]]         # only the missing one, same seeds

    # A different game count or reference bot is a different measurement: replay everything.
    assert len(_run(probe, monkeypatch, tmp_path, "--games", "20", "--resume", configs=configs)) == 60
    assert len(_run(probe, monkeypatch, tmp_path, "--games", "20", "--ref", "anchor", "--resume",
                    configs=configs)) == 60
    assert _run(probe, monkeypatch, tmp_path, "--games", "20", "--ref", "anchor", "--resume",
                configs=configs) == []                                     # ...and that one is now reusable


def test_a_typo_in_a_later_config_fails_before_any_game_is_played(probe, monkeypatch, tmp_path):
    played = []
    monkeypatch.setattr(probe, "play_one", lambda job: played.append(job))
    monkeypatch.setattr(sys, "argv", ["ladder_dial_probe.py", "--out", str(tmp_path / "out.json"),
                                      "--configs", "gen7:16x2:e0", "gen7:oops"])
    with pytest.raises(ValueError, match="bad config 'gen7:oops'"):
        probe.main()
    assert played == []


def test_a_real_game_runs_to_a_deterministic_result(probe):
    # heuristic vs heuristic is near-instant: this exercises the real play_one wiring (make, seat
    # assignment, the game loop) that the stand-in games above skip.
    for seed in (3, 4, 10, 11):                            # both seat parities
        first = probe.play_one((seed, "heuristic", "heuristic"))
        assert isinstance(first, bool)
        assert probe.play_one((seed, "heuristic", "heuristic")) == first


def test_the_config_under_test_alternates_seats_and_a_win_is_its_win(probe, monkeypatch):
    seat_of, finished = {}, []

    class Tagged(HeuristicAgent):                          # plays like the heuristic, remembers its seat
        def __init__(self, tag):
            super().__init__()
            self.tag = tag

        def begin_game(self, seat):
            seat_of[self.tag] = seat
            super().begin_game(seat)

    real_new_game = probe.new_game
    monkeypatch.setattr(probe, "new_game", lambda seed: finished.append(real_new_game(seed)) or finished[-1])
    monkeypatch.setattr(probe, "make", lambda cfg, seed: Tagged(cfg))
    outcomes = set()
    for seed in (3, 4, 10, 11):                            # both seat parities, both winners
        won = probe.play_one((seed, "tested", "reference"))
        assert seat_of == {"tested": seed % 2, "reference": 1 - seed % 2}
        assert won == (finished[-1].winner == seed % 2)    # True iff the tested config's seat won
        outcomes.add(won)
    assert outcomes == {True, False}                       # the check saw both results
