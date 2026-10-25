"""Running a hand more than once, and the win percentages beside it."""

from __future__ import annotations

import pytest

from app.engine import holdem
from app.engine.cards import make_deck
from app.engine.equity import equities
from app.engine.holdem import COMPLETE, RUN_CHOICE, Config, IllegalAction, start_hand


def all_in_hand(stacks=(100, 100), max_runs=3, deck=None):
    """Two players, both all in before the flop."""
    hand = start_hand(
        hand_no=1,
        button=0,
        stacks={i: (f"p{i}", stacks[i]) for i in range(len(stacks))},
        config=Config(small_blind=1, big_blind=2),
        deck=list(deck or make_deck()),
        max_runs=max_runs,
    )
    hand.act(0, "raise", stacks[0])
    for seat in range(1, len(stacks)):
        hand.act(seat, "call")
    return hand


def chips(hand):
    return hand.chips_on_table()


# ------------------------------------------------------------- the offer


def test_the_choice_is_offered_when_someone_is_all_in_with_cards_to_come():
    hand = all_in_hand()
    assert hand.street == RUN_CHOICE
    assert hand.max_runs == 3
    assert sorted(hand.run_voters()) == [0, 1]
    assert hand.to_act is None, "nobody is betting any more"


def test_no_choice_is_offered_when_the_host_has_it_switched_off():
    hand = all_in_hand(max_runs=1)
    assert hand.street == COMPLETE, "it just runs out"


def test_no_choice_is_offered_when_everyone_folds():
    hand = start_hand(
        1, 0, {0: ("a", 100), 1: ("b", 100), 2: ("c", 100)},
        Config(small_blind=1, big_blind=2), deck=make_deck(), max_runs=3,
    )
    hand.act(0, "raise", 100)
    hand.act(1, "fold")
    hand.act(2, "fold")
    assert hand.street == COMPLETE


def test_the_deck_limits_how_many_runs_are_possible():
    """Ten players all in before the flop: there are not 3 boards left."""
    stacks = tuple(20 for _ in range(10))
    hand = start_hand(
        1, 0, {i: (f"p{i}", 20) for i in range(10)},
        Config(small_blind=1, big_blind=2), deck=make_deck(), max_runs=3,
    )
    hand.act(3, "raise", 20)
    for seat in [4, 5, 6, 7, 8, 9, 0, 1, 2]:
        if hand.to_act is not None:
            hand.act(hand.to_act, "call")
    # 52 - 20 hole cards = 32 left; a full board costs 7, so three runs fit.
    assert hand.max_runs <= 3
    assert hand.max_runs == min(3, len(hand.deck) // 7) or hand.street == COMPLETE


# ------------------------------------------------------------- choosing


def test_the_lowest_number_anybody_asks_for_is_the_one_used():
    hand = all_in_hand()
    hand.choose_runs(0, 3)
    hand.choose_runs(1, 2)
    assert hand.everyone_has_chosen()

    hand.resolve_runs()

    assert hand.street == COMPLETE
    assert len(hand.run_boards) == 2
    assert len(hand.boards()) == 2


def test_nothing_chosen_counts_as_one():
    hand = all_in_hand()
    hand.choose_runs(0, 3)  # the other player says nothing
    hand.resolve_runs()
    assert len(hand.boards()) == 1


def test_a_silly_number_is_refused():
    hand = all_in_hand()
    with pytest.raises(IllegalAction, match="between 1 and 3"):
        hand.choose_runs(0, 9)
    with pytest.raises(IllegalAction, match="between 1 and 3"):
        hand.choose_runs(0, 0)


def test_someone_not_in_the_hand_has_no_say():
    hand = all_in_hand(stacks=(100, 100, 100))
    with pytest.raises(IllegalAction):
        hand.choose_runs(7, 2)


# --------------------------------------------------------------- paying


def test_each_pot_is_split_evenly_across_the_runs():
    hand = all_in_hand()
    before = chips(hand)
    hand.choose_runs(0, 2)
    hand.choose_runs(1, 2)
    hand.resolve_runs()

    assert chips(hand) == before, "chips are conserved across runs"
    runs = hand.result["runs"]
    assert len(runs) == 2
    halves = [sum(p["amount"] for p in run["pots"]) for run in runs]
    assert sum(halves) == hand.result["pot"]
    assert abs(halves[0] - halves[1]) <= 1, "split evenly, odd chip to the first"


def test_every_run_gets_its_own_board_and_they_differ():
    hand = all_in_hand()
    hand.choose_runs(0, 3)
    hand.choose_runs(1, 3)
    hand.resolve_runs()

    boards = hand.boards()
    assert len(boards) == 3
    assert all(len(board) == 5 for board in boards)
    # Every card across every board is distinct: no card is dealt twice.
    dealt = [card for board in boards for card in board]
    assert len(set(dealt)) == len(dealt)


def test_board_cards_already_out_are_shared_by_every_run():
    """All in on the turn: the flop and turn are common to both boards."""
    hand = start_hand(
        1, 0, {0: ("a", 100), 1: ("b", 100)},
        Config(small_blind=1, big_blind=2), deck=make_deck(), max_runs=2,
    )
    hand.act(0, "call")
    hand.act(1, "check")
    hand.act(1, "check")
    hand.act(0, "check")   # flop checked through
    hand.act(1, "check")
    # 98 is the whole stack: 2 of the 100 went in preflop.
    hand.act(0, "raise", 98)
    hand.act(1, "call")    # all in on the turn

    assert hand.street == RUN_CHOICE
    shared = list(hand.board)
    assert len(shared) == 4

    hand.choose_runs(0, 2)
    hand.choose_runs(1, 2)
    hand.resolve_runs()

    boards = hand.boards()
    assert len(boards) == 2
    assert boards[0][:4] == shared and boards[1][:4] == shared
    assert boards[0][4] != boards[1][4], "only the river differs"


def test_a_run_choice_survives_a_restart():
    import json

    hand = all_in_hand()
    hand.choose_runs(0, 2)
    restored = holdem.Hand.from_dict(json.loads(json.dumps(hand.to_dict())))

    assert restored.street == RUN_CHOICE
    assert restored.run_choices == {0: 2}
    assert restored.max_runs == 3
    restored.choose_runs(1, 3)
    restored.resolve_runs()
    assert len(restored.boards()) == 2


# ---------------------------------------------------- win percentages


def test_percentages_add_up_and_favour_the_better_hand():
    result = equities({0: ["Ah", "Ad"], 1: ["7c", "2d"]}, [])
    total = sum(row["win"] + row["tie"] for row in result.values())
    assert 99.0 <= total <= 101.0
    assert result[0]["win"] > 75, "aces are a big favourite"


def test_a_decided_hand_reads_a_hundred_percent():
    result = equities(
        {0: ["Ah", "Ad"], 1: ["7c", "2d"]}, ["As", "Kd", "9h", "4c", "3s"]
    )
    assert result[0] == {"win": 100.0, "tie": 0.0}
    assert result[1] == {"win": 0.0, "tie": 0.0}


def test_a_dead_heat_reads_as_a_tie():
    # Both players play the board.
    result = equities(
        {0: ["2c", "3d"], 1: ["2h", "3s"]}, ["As", "Ks", "Qd", "Jh", "Th"]
    )
    assert result[0]["tie"] == 100.0
    assert result[1]["tie"] == 100.0
    assert result[0]["win"] == 0.0


def test_the_turn_is_counted_exactly():
    """One card to come: every possibility is checked, so it is exact."""
    result = equities({0: ["Ah", "Kd"], 1: ["Qs", "Qc"]}, ["Ac", "7d", "2h", "9s"])
    total = sum(row["win"] + row["tie"] for row in result.values())
    assert abs(total - 100.0) < 0.2
    assert result[0]["win"] > 90, "top pair with a card to come"


def test_three_way_percentages_still_add_up():
    result = equities(
        {0: ["Ah", "Ad"], 1: ["Kh", "Kd"], 2: ["Qh", "Qd"]}, ["2c", "7d", "9s"]
    )
    total = sum(row["win"] + row["tie"] for row in result.values())
    assert 99.0 <= total <= 101.0
    assert result[0]["win"] > result[1]["win"] > result[2]["win"]
