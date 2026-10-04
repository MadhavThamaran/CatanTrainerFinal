"""Suite-wide fixtures."""
import pytest

# Every module that reads or writes play-mode game records (data/games/).
_GAME_RECORD_DIRS = (
    "trainer.play._GAMES_DIR",
    "trainer.review._GAMES_DIR",
    "trainer.analysis._GAMES_DIR",
    "trainer.dashboard.GAMES_DIR",
)


@pytest.fixture(autouse=True)
def _isolate_game_records(tmp_path, monkeypatch):
    """Keep every test out of the real data/games/.

    A finished play session persists a replayable record there, and the
    dashboard counts everything in that folder as the player's games, so a
    test that finishes a game would leave a stray loss in the user's history
    on every run. Tests that care where records go still override this with
    their own monkeypatch.
    """
    games = tmp_path / "games"
    for target in _GAME_RECORD_DIRS:
        monkeypatch.setattr(target, games)
