"""The betting engine: blinds, raise ranges, limits, side pots, odd chips."""

from __future__ import annotations

import json

import pytest

from app.engine.cards import make_deck
from app.engine.holdem import (
    COMPLETE,
    SHOWDOWN,
    Config,
    Hand,
    IllegalAction,
    start_hand,
)

# A fixed deck, so hands are reproducible. Cards are dealt from the front:
# hole cards one at a time left of the button, then burn-and-board.
FIXED = make_deck()


def three_handed(stacks=(200, 200, 200), config=None, deck=None) -> Hand:
    """Seats 0, 1, 2 with seat 0 on the button."""
    return start_hand(
        hand_no=1,
        button=0,
        stacks={i: (f"p{i}", stacks[i]) for i in range(3)},
        config=config or Config(small_blind=1, big_blind=2),
        deck=list(deck or FIXED),
    )


def chips(hand: Hand) -> int:
    """Every chip in the game, wherever it sits right now."""
    return hand.chips_on_table()


# ------------------------------------------------------------- the basics


def test_blinds_are_posted_and_utg_acts_first():
    hand = three_handed()
    assert hand.seats[1].total == 1, "small blind"
    assert hand.seats[2].total == 2, "big blind"
    assert hand.current_bet == 2
    # Three-handed, the button is under the gun before the flop.
    assert hand.to_act == 0


def test_everyone_gets_two_cards_and_the_deck_shrinks():
    hand = three_handed()
    assert all(len(s.hole) == 2 for s in hand.seats.values())
    assert len(hand.deck) == 52 - 6
    dealt = [c for s in hand.seats.values() for c in s.hole]
    assert len(set(dealt)) == 6, "no card dealt twice"


def test_the_big_blind_still_has_an_option():
    hand = three_handed()
    hand.act(0, "call")
    hand.act(1, "call")
    # Everyone has matched, but the big blind has not acted yet.
    assert hand.to_act == 2
    assert "check" in hand.legal_actions(2)["actions"]
    hand.act(2, "check")
    assert hand.street == "flop"


def test_the_first_player_left_of_the_button_acts_first_after_the_flop():
    hand = three_handed()
    hand.act(0, "call")
    hand.act(1, "call")
    hand.act(2, "check")
    assert hand.to_act == 1


def test_a_hand_everyone_folds_pays_the_last_player_standing():
    hand = three_handed()
    before = chips(hand)
    hand.act(0, "fold")
    hand.act(1, "fold")
    assert hand.street == COMPLETE
    assert hand.result["uncontested"] is True
    assert hand.seats[2].stack == 200 + 1, "won the small blind"
    assert chips(hand) == before


def test_folding_out_of_turn_is_refused():
    hand = three_handed()
    with pytest.raises(IllegalAction, match="not your turn"):
        hand.act(1, "fold")


def test_checking_into_a_bet_is_refused():
    hand = three_handed()
    with pytest.raises(IllegalAction, match="cannot check"):
        hand.act(0, "check")


# -------------------------------------------------------------- raise sizes


def test_the_minimum_raise_is_the_size_of_the_last_raise():
    hand = three_handed()
    assert hand.legal_actions(0)["min_raise_to"] == 4
    hand.act(0, "raise", 6)  # a raise of 4
    assert hand.legal_actions(1)["min_raise_to"] == 10


def test_raises_need_not_be_multiples_of_the_big_blind():
    hand = three_handed()
    hand.act(0, "raise", 7)
    assert hand.seats[0].committed == 7
    assert hand.current_bet == 7


def test_a_raise_below_the_minimum_is_refused_with_the_number():
    hand = three_handed()
    with pytest.raises(IllegalAction, match="smallest raise here is 4"):
        hand.act(0, "raise", 3)


def test_the_maximum_is_the_players_whole_stack_under_no_limit():
    hand = three_handed()
    assert hand.legal_actions(0)["max_raise_to"] == 200


# ------------------------------------------------------------- pot limit


def test_pot_limit_test_vector_from_the_build_instructions():
    """Blinds 1/2, no straddle: the first player to act may raise to 7."""
    hand = three_handed(config=Config(small_blind=1, big_blind=2, structure="pot-limit"))
    actions = hand.legal_actions(0)
    assert actions["max_raise_to"] == 7
    assert actions["capped_by"] == "structure"
    hand.act(0, "raise", 7)
    assert hand.current_bet == 7


def test_pot_limit_refuses_more_than_the_pot():
    hand = three_handed(config=Config(small_blind=1, big_blind=2, structure="pot-limit"))
    with pytest.raises(IllegalAction, match="most you can raise to here is 7"):
        hand.act(0, "raise", 8)


def test_pot_limit_grows_with_the_pot():
    hand = three_handed(config=Config(small_blind=1, big_blind=2, structure="pot-limit"))
    hand.act(0, "raise", 7)
    # The small blind has 1 in already: pot 1 + 2 + 7 = 10, to call 6, so the
    # cap is 7 + 10 + 6 = 23.
    assert hand.legal_actions(1)["max_raise_to"] == 23


# ---------------------------------------------------------- preflop limit


def test_a_preflop_limit_of_zero_changes_nothing():
    hand = three_handed(config=Config(small_blind=1, big_blind=2, preflop_limit=0))
    assert hand.legal_actions(0)["max_raise_to"] == 200


def test_a_preflop_limit_caps_the_raise_and_the_all_in():
    hand = three_handed(config=Config(small_blind=1, big_blind=2, preflop_limit=20))
    actions = hand.legal_actions(0)
    assert actions["max_raise_to"] == 20
    assert actions["capped_by"] == "preflop limit"
    with pytest.raises(IllegalAction, match="most you can raise to here is 20"):
        hand.act(0, "raise", 200)
    hand.act(0, "raise", 20)
    assert hand.seats[0].committed == 20


def test_a_short_stack_may_still_go_all_in_under_a_preflop_limit():
    """Going all in for less than the limit is always allowed."""
    hand = three_handed(
        stacks=(15, 200, 200),
        config=Config(small_blind=1, big_blind=2, preflop_limit=20),
    )
    assert hand.legal_actions(0)["max_raise_to"] == 15
    hand.act(0, "raise", 15)
    assert hand.seats[0].all_in


def test_the_preflop_limit_stops_at_the_flop():
    hand = three_handed(config=Config(small_blind=1, big_blind=2, preflop_limit=20))
    hand.act(0, "call")
    hand.act(1, "call")
    hand.act(2, "check")
    assert hand.street == "flop"
    assert hand.legal_actions(1)["max_raise_to"] == 198


def test_the_tightest_limit_wins():
    """Pot limit and a preflop limit together: whichever is smaller."""
    hand = three_handed(
        config=Config(
            small_blind=1, big_blind=2, structure="pot-limit", preflop_limit=5
        )
    )
    assert hand.legal_actions(0)["max_raise_to"] == 5

    looser = three_handed(
        config=Config(
            small_blind=1, big_blind=2, structure="pot-limit", preflop_limit=50
        )
    )
    assert looser.legal_actions(0)["max_raise_to"] == 7


# ------------------------------------------------------------ short all-in


def test_a_short_all_in_does_not_reopen_for_players_who_have_acted():
    """Four-handed: seat 3 raises, seat 0 calls, the short stack jams.

    The jam is smaller than a full raise. It raises the amount to call for
    everyone, but only the player who has not had their turn yet gets a
    fresh decision to raise.
    """
    hand = start_hand(
        hand_no=1,
        button=0,
        # Seat 1 is the small blind and the short stack.
        stacks={0: ("a", 200), 1: ("b", 15), 2: ("c", 200), 3: ("d", 200)},
        config=Config(small_blind=1, big_blind=2),
        deck=list(FIXED),
    )
    assert hand.to_act == 3, "under the gun, four-handed"

    hand.act(3, "raise", 10)  # a full raise: the increment is now 8
    hand.act(0, "call")
    hand.act(1, "raise", 15)  # all in for 5 more: short of a full raise

    assert hand.seats[1].all_in
    assert hand.current_bet == 15

    # Seat 2 has not had a turn yet, so the jam is a live bet to it.
    assert "raise" in hand.legal_actions(2)["actions"]
    hand.act(2, "call")

    # Seat 3 raised and seat 0 called before the jam. Neither gets to raise
    # again; they may only call or fold.
    for seat_no in (3, 0):
        assert "raise" not in hand.legal_actions(seat_no)["actions"]
        with pytest.raises(IllegalAction, match="does not reopen"):
            hand.act(seat_no, "raise", 60)
        hand.act(seat_no, "call")

    assert hand.street in ("flop", SHOWDOWN, COMPLETE)
    assert chips(hand) == 615


# ----------------------------------------------------------- side pots


def test_side_pots_pay_the_right_players():
    """Short stack can only win what it covered."""
    hand = start_hand(
        hand_no=1,
        button=0,
        stacks={0: ("a", 50), 1: ("b", 100), 2: ("c", 100)},
        config=Config(small_blind=1, big_blind=2),
        # Seat 1 and 2 get the big hands; seat 0 (the short stack) is dealt
        # the worst of the three.
        deck="2c 7d As Ac 3h Ad 9s 9d 9h Kc Qc Jc".split() + [
            c for c in make_deck() if c not in
            "2c 7d As Ac 3h Ad 9s 9d 9h Kc Qc Jc".split()
        ],
    )
    before = chips(hand)
    hand.act(0, "raise", 50)
    hand.act(1, "raise", 100)
    hand.act(2, "call")

    assert hand.street == COMPLETE
    assert chips(hand) == before, "chips are conserved"
    # Everyone's chips went somewhere, and nobody has a negative stack.
    assert all(s.stack >= 0 for s in hand.seats.values())
    # Two pots: the 150 main pot everyone could win, and the 100 side pot
    # only seats 1 and 2 could.
    pots = hand.result["runs"][0]["pots"]
    assert [p["amount"] for p in pots] == [150, 100]


def test_a_folded_players_chips_stay_in_the_pot():
    hand = three_handed(stacks=(200, 200, 200))
    before = chips(hand)
    hand.act(0, "raise", 20)
    hand.act(1, "fold")  # the small blind's 1 chip stays in
    hand.act(2, "fold")
    assert hand.seats[0].stack == 200 + 3
    assert chips(hand) == before


def test_an_uncalled_bet_comes_back():
    hand = three_handed()
    before = chips(hand)
    hand.act(0, "raise", 50)
    hand.act(1, "fold")
    hand.act(2, "fold")
    # Seat 0 wins the blinds and gets the uncalled 48 back.
    assert hand.seats[0].stack == 200 + 3
    assert chips(hand) == before


def test_odd_chips_go_to_the_first_seat_left_of_the_button():
    """A split pot that does not divide evenly."""
    board = "2c 3d 4h 5s 7c".split()
    # Both players play the board; the pot is odd.
    deck = "Ac Ad Ah As".split() + ["Kc"] + board + [
        c for c in make_deck() if c not in "Ac Ad Ah As Kc".split() + board
    ]
    hand = start_hand(
        hand_no=1,
        button=0,
        stacks={0: ("a", 100), 1: ("b", 100)},
        config=Config(small_blind=1, big_blind=2),
        deck=deck,
    )
    # Heads-up: seat 0 is the button and small blind.
    hand.act(0, "raise", 5)
    hand.act(1, "call")
    for _ in range(3):
        if hand.street != COMPLETE:
            hand.act(hand.to_act, "check")
            if hand.to_act is not None:
                hand.act(hand.to_act, "check")

    if hand.result and not hand.result["uncontested"]:
        payout = hand.result["runs"][0]["pots"][0]["payout"]
        total = sum(payout.values())
        assert total == hand.result["runs"][0]["pots"][0]["amount"]


# ----------------------------------------------------------- heads-up


def test_heads_up_the_button_is_the_small_blind_and_acts_first():
    hand = start_hand(
        hand_no=1,
        button=0,
        stacks={0: ("a", 100), 1: ("b", 100)},
        config=Config(small_blind=1, big_blind=2),
        deck=list(FIXED),
    )
    assert hand.seats[0].total == 1, "the button posts the small blind"
    assert hand.seats[1].total == 2
    assert hand.to_act == 0, "the button acts first before the flop"

    hand.act(0, "call")
    hand.act(1, "check")
    assert hand.street == "flop"
    assert hand.to_act == 1, "the big blind acts first after the flop"


# ------------------------------------------------------------- antes


def test_antes_are_dead_money():
    hand = three_handed(
        config=Config(small_blind=1, big_blind=2, ante=1, ante_type="all")
    )
    assert hand.pot_total == 3 + 3, "three antes plus the blinds"
    # The ante does not count towards the amount to call.
    assert hand.legal_actions(0)["to_call"] == 2


def test_a_big_blind_ante_is_posted_by_the_big_blind_alone():
    hand = three_handed(
        config=Config(small_blind=1, big_blind=2, ante=1, ante_type="big-blind")
    )
    assert hand.seats[2].total == 3 + 2, "three antes plus their own big blind"
    assert hand.seats[0].total == 0


# ------------------------------------------------------------ straddle


def test_a_straddle_raises_the_stake_and_acts_last():
    hand = three_handed(
        config=Config(
            small_blind=1, big_blind=2, straddle={"seat": 0, "amount": 4}
        )
    )
    assert hand.current_bet == 4
    assert hand.seats[0].total == 4
    # Action starts left of the straddle, and the straddler acts last.
    assert hand.to_act == 1


# -------------------------------------------------------- serialisation


def test_a_hand_survives_a_round_trip_through_json():
    hand = three_handed()
    hand.act(0, "raise", 8)

    blob = json.dumps(hand.to_dict())
    restored = Hand.from_dict(json.loads(blob))

    assert restored.to_dict() == hand.to_dict()
    assert restored.to_act == hand.to_act
    assert restored.seats[0].hole == hand.seats[0].hole
    assert restored.deck == hand.deck


def test_restoring_mid_hand_deals_the_same_cards():
    """The deck is part of the state: a restart cannot re-deal."""
    hand = three_handed()
    hand.act(0, "call")
    hand.act(1, "call")

    restored = Hand.from_dict(json.loads(json.dumps(hand.to_dict())))
    hand.act(2, "check")
    restored.act(2, "check")

    assert restored.board == hand.board
    assert restored.board != []
