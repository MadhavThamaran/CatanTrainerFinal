"""Board generation constraints (rules.md §4): multisets, 6/8 and 2/12
non-adjacency, port validity, serialization round-trip."""
import random

from engine import Board, TOPOLOGY, generate_board, validate_board


def test_generated_boards_are_valid_across_seeds():
    for seed in range(50):
        board = generate_board(random.Random(seed))
        assert validate_board(board) == [], f"seed {seed}"


def test_validator_catches_high_number_adjacency():
    board = generate_board(random.Random(0))
    # Force a 6 next to an 8 by swapping numbers (multisets preserved).
    six = board.numbers.index(6)
    eight = board.numbers.index(8)
    neighbor = TOPOLOGY.hex_neighbors[six][0]
    if neighbor == eight or board.numbers[neighbor] is None:
        neighbor = next(
            h for h in TOPOLOGY.hex_neighbors[six]
            if h != eight and board.numbers[h] is not None
        )
    board.numbers[neighbor], board.numbers[eight] = (
        board.numbers[eight],
        board.numbers[neighbor],
    )
    assert any("adjacency" in v for v in validate_board(board))


def test_validator_catches_low_number_adjacency():
    board = generate_board(random.Random(1))
    two = board.numbers.index(2)
    twelve = board.numbers.index(12)
    neighbor = next(
        h for h in TOPOLOGY.hex_neighbors[two]
        if h != twelve and board.numbers[h] is not None
    )
    board.numbers[neighbor], board.numbers[twelve] = (
        board.numbers[twelve],
        board.numbers[neighbor],
    )
    assert any("adjacency" in v for v in validate_board(board))


def test_validator_catches_wrong_multisets():
    board = generate_board(random.Random(2))
    board.numbers[board.numbers.index(2)] = 9
    assert any("number multiset" in v for v in validate_board(board))


def test_board_serialization_round_trip():
    board = generate_board(random.Random(3))
    restored = Board.from_dict(board.to_dict())
    assert restored.terrain == board.terrain
    assert restored.numbers == board.numbers
    assert restored.ports == board.ports
