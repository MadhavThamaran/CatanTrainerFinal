"""scripts/ladder_calibrate.py (LADDER_SPEC §5). The real run is hours long and
unattended, so what it must get right without anyone watching — the seed range
it consumes, resuming a killed run (and never resuming across a re-specced
rung), writing something the ladder can load, smoothing noise, a fit that
recovers the ratings — is checked here with a stand-in for the games."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from trainer import ladder

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ladder_calibrate.py"
N_PAIRS = 15   # 7 adjacent rung pairs + every one of the 8 rungs against the anchor


@pytest.fixture
def cal(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ladder_calibrate", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_OUT", tmp_path / "ladder_calibration.json")
    monkeypatch.setattr(mod, "_RESULTS_OUT", tmp_path / "ladder_calibration_results.json")
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


def _fake_game(job):
    """Rung `a` wins when its game slot (0-59) falls under the win rate that the
    pair's declared rating gap predicts — a pure function of the job."""
    seed, a, b = job
    gap = ladder.get_rung(b).provisional_elo - ladder.get_rung(a).provisional_elo
    return (seed % 1000) / 60 < 1.0 / (1.0 + 10 ** (gap / 400.0))


def _run(cal, monkeypatch, *flags, play=_fake_game):
    played = []

    def recording_play(job):
        played.append(job)
        return play(job)

    monkeypatch.setattr(cal, "play_one", recording_play)
    monkeypatch.setattr(cal.mp, "Pool", _InlinePool)
    monkeypatch.setattr(sys, "argv", ["ladder_calibrate.py", "--games", "60", *flags])
    cal.main()
    return played


def test_pairs_are_adjacent_rungs_then_every_rung_against_the_anchor(cal):
    pairs = cal._pairs()
    assert len(pairs) == N_PAIRS
    assert pairs[:7] == [(n, n + 1) for n in range(1, 8)]
    assert pairs[7:] == [(n, ladder.ANCHOR_NUMBER) for n in range(1, 9)]


def test_a_default_run_stays_inside_the_ledger_range(cal, monkeypatch):
    seeds = [seed for seed, _, _ in _run(cal, monkeypatch)]
    assert len(seeds) == N_PAIRS * 60
    assert len(set(seeds)) == len(seeds)                 # no seed shared by two games
    assert (min(seeds), max(seeds)) == (92_000_000, 92_014_059)


def test_the_output_loads_as_the_ladders_measured_ratings(cal, monkeypatch):
    _run(cal, monkeypatch)
    raw = json.loads(cal._OUT.read_text())
    assert sorted(raw["config"]) == [str(n) for n in range(1, 9)]       # the anchor is not a rung
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", cal._OUT)
    measured = ladder._load_calibration()
    assert sorted(measured) == list(range(1, 9))
    # The games followed the declared gaps, so the fit lands near them, in order.
    # 60-game win counts are coarse at the extremes (a 1% rate is 0 or 1 wins),
    # so this only catches gross errors; the exact recovery check is below.
    for n in range(1, 9):
        assert measured[n] == pytest.approx(ladder.get_rung(n).provisional_elo, abs=75)
    assert all(measured[n] <= measured[n + 1] for n in range(1, 8))


def test_a_fitted_inversion_is_pooled_in_the_written_ratings(cal, monkeypatch):
    strength = {n: ladder.get_rung(n).provisional_elo for n in range(0, 9)}
    strength[4], strength[5] = strength[5], strength[4]      # rung 4 secretly plays stronger than 5

    def swapped_game(job):
        seed, a, b = job
        return (seed % 1000) / 60 < 1.0 / (1.0 + 10 ** ((strength[b] - strength[a]) / 400.0))

    _run(cal, monkeypatch, play=swapped_game)
    raw = json.loads(cal._OUT.read_text())
    assert raw["smoothed"] == [[4, 5]]                       # the inversion was reported...
    assert raw["elo"]["4"] == raw["elo"]["5"]                # ...and pooled, not written as measured
    ordered = [raw["elo"][str(n)] for n in range(1, 9)]
    assert ordered == sorted(ordered)


def test_a_measured_rating_is_ignored_once_its_rung_has_changed(cal, monkeypatch):
    _run(cal, monkeypatch)
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", cal._OUT)
    assert sorted(ladder._load_calibration()) == list(range(1, 9))
    r5 = ladder.get_rung(5)
    # Re-spec rung 5 (a different random-move rate = a different bot).
    monkeypatch.setitem(ladder._BY_NUMBER, 5, type(r5)(**{**r5.__dict__, "epsilon": r5.epsilon + 0.05}))
    assert sorted(ladder._load_calibration()) == [1, 2, 3, 4, 6, 7, 8]    # 5 falls back to its target


def test_resume_replays_only_missing_pairs_on_their_original_seeds(cal, monkeypatch):
    first = _run(cal, monkeypatch)
    saved = json.loads(cal._RESULTS_OUT.read_text())
    assert len(saved["pairs"]) == N_PAIRS
    del saved["pairs"]["4,5"]
    cal._RESULTS_OUT.write_text(json.dumps(saved))

    again = _run(cal, monkeypatch, "--resume")
    assert again == [job for job in first if job[1:] == (4, 5)]    # same pair, same seeds
    assert _run(cal, monkeypatch, "--resume") == []                # nothing left to play


def test_resume_replays_a_pair_whose_bot_has_since_changed(cal, monkeypatch):
    first = _run(cal, monkeypatch)
    saved = json.loads(cal._RESULTS_OUT.read_text())
    saved["pairs"]["6,7"]["sig"][0] = "00000000dead"               # rung 6 was a different bot then
    cal._RESULTS_OUT.write_text(json.dumps(saved))
    again = _run(cal, monkeypatch, "--resume")
    assert again == [job for job in first if job[1:] == (6, 7)]


def test_resume_ignores_a_results_file_from_the_old_format(cal, monkeypatch):
    cal._RESULTS_OUT.write_text(json.dumps({"1,2": [29, 60], "4,5": [27, 60]}))   # the v1 layout
    played = _run(cal, monkeypatch, "--resume")
    assert len(played) == N_PAIRS * 60                             # nothing was trusted


def test_monotone_smoothing_leaves_ordered_ratings_alone(cal):
    ordered = {n: 1000.0 + 50 * n for n in range(1, 9)}
    smoothed, pooled = cal._monotone(ordered)
    assert smoothed == ordered and pooled == []


def test_monotone_smoothing_pools_an_inversion_to_its_mean(cal):
    elo = {n: 1000.0 + 100 * n for n in range(1, 9)}
    elo[4], elo[5] = 1450.0, 1410.0                       # inverted by noise
    smoothed, pooled = cal._monotone(elo)
    assert pooled == [[4, 5]]
    assert smoothed[4] == smoothed[5] == pytest.approx(1430.0)
    assert all(smoothed[n] == elo[n] for n in (1, 2, 3, 6, 7, 8))


def test_monotone_smoothing_cascades_through_a_chain_of_violations(cal):
    elo = {1: 1000.0, 2: 1300.0, 3: 1200.0, 4: 1100.0, 5: 1500.0, 6: 1600.0, 7: 1650.0, 8: 1700.0}
    smoothed, pooled = cal._monotone(elo)
    assert pooled == [[2, 3, 4]]
    assert smoothed[2] == smoothed[3] == smoothed[4] == pytest.approx(1200.0)
    assert all(smoothed[n] <= smoothed[n + 1] for n in range(1, 8))


def test_fit_recovers_the_ratings_the_results_came_from(cal):
    truth = {0: 1200.0, 1: 850.0, 2: 930.0, 3: 1010.0, 4: 1100.0, 5: 1190.0, 6: 1260.0,
             7: 1330.0, 8: 1390.0}
    results = {}
    for a, b in cal._pairs():
        expected = 1.0 / (1.0 + 10 ** ((truth[b] - truth[a]) / 400.0))
        results[(a, b)] = (60 * expected, 60)            # exact expected wins, no sampling noise
    fit = cal._fit(results)
    assert fit[0] == 1200.0                              # the anchor never moves
    for n, elo in truth.items():
        assert fit[n] == pytest.approx(elo, abs=1.0)
