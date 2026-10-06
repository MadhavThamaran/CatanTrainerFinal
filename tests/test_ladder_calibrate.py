"""scripts/ladder_calibrate.py (LADDER_SPEC §5). The real run is ~8-10h and
unattended, so what it must get right without anyone watching — the seed range
it consumes, resuming a killed run, writing something the ladder can load, a fit
that recovers the ratings — is checked here with a stand-in for the games."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from trainer import ladder

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ladder_calibrate.py"
N_PAIRS = 12   # 7 adjacent + 5 anchored on rung 3 (rung 3's own pairs deduped)


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


def test_a_default_run_stays_inside_the_ledger_range(cal, monkeypatch):
    seeds = [seed for seed, _, _ in _run(cal, monkeypatch)]
    assert len(seeds) == N_PAIRS * 60
    assert len(set(seeds)) == len(seeds)                 # no seed shared by two games
    assert (min(seeds), max(seeds)) == (91_000_000, 91_011_059)


def test_the_output_loads_as_the_ladders_measured_ratings(cal, monkeypatch):
    _run(cal, monkeypatch)
    monkeypatch.setattr(ladder, "_CALIBRATION_PATH", cal._OUT)
    measured = ladder._load_calibration()
    assert sorted(measured) == list(range(1, 9))
    assert measured[3] == 1200.0                         # the pinned anchor
    # The games followed the declared gaps, so the fit lands near them, in order.
    # 60-game win counts are coarse at the extremes (a 1% rate is 0 or 1 wins),
    # so this only catches gross errors; the exact recovery check is below.
    for n in range(1, 9):
        assert measured[n] == pytest.approx(ladder.get_rung(n).provisional_elo, abs=75)
    assert all(measured[n] < measured[n + 1] for n in range(1, 8))


def test_resume_replays_only_missing_pairs_on_their_original_seeds(cal, monkeypatch):
    first = _run(cal, monkeypatch)
    results = json.loads(cal._RESULTS_OUT.read_text())
    assert len(results) == N_PAIRS
    del results["4,5"]
    cal._RESULTS_OUT.write_text(json.dumps(results))

    again = _run(cal, monkeypatch, "--resume")
    assert again == [job for job in first if job[1:] == (4, 5)]    # same pair, same seeds
    assert _run(cal, monkeypatch, "--resume") == []                # nothing left to play


def test_fit_recovers_the_ratings_the_results_came_from(cal):
    truth = {1: 800.0, 2: 1000.0, 3: 1200.0, 4: 1300.0, 5: 1500.0, 6: 1550.0, 7: 1800.0, 8: 1900.0}
    results = {}
    for a, b in cal._pairs():
        expected = 1.0 / (1.0 + 10 ** ((truth[b] - truth[a]) / 400.0))
        results[(a, b)] = (60 * expected, 60)            # exact expected wins, no sampling noise
    fit = cal._fit(results)
    for n, elo in truth.items():
        assert fit[n] == pytest.approx(elo, abs=1.0)
