"""Hand ranking, checked against hand-written cases and against PokerKit."""

from __future__ import annotations

import random

import pytest

from app.engine.evaluator import (
    FLUSH,
    FULL_HOUSE,
    HIGH_CARD,
    PAIR,
    QUADS,
    STRAIGHT,
    STRAIGHT_FLUSH,
    TRIPS,
    TWO_PAIR,
    best_five,
    describe,
    evaluate,
)
from app.engine.cards import make_deck


def cat(cards: str) -> int:
    return evaluate(cards.split())[0]


def test_every_category_is_recognised():
    assert cat("As Ks Qs Js Ts") == STRAIGHT_FLUSH
    assert cat("9c 9d 9h 9s 2c") == QUADS
    assert cat("8c 8d 8h 3s 3c") == FULL_HOUSE
    assert cat("Ah Jh 9h 5h 2h") == FLUSH
    assert cat("9c 8d 7h 6s 5c") == STRAIGHT
    assert cat("Qc Qd Qh 4s 2c") == TRIPS
    assert cat("Kc Kd 7h 7s 2c") == TWO_PAIR
    assert cat("Ac Ad 9h 5s 2c") == PAIR
    assert cat("Ac Jd 9h 5s 2c") == HIGH_CARD


def test_the_wheel_is_a_straight_with_the_five_on_top():
    wheel = evaluate("Ac 2d 3h 4s 5c".split())
    six_high = evaluate("2c 3d 4h 5s 6c".split())
    assert wheel[0] == STRAIGHT
    assert wheel < six_high
    # And it loses to a pair of aces? No: a straight always beats a pair.
    assert wheel > evaluate("Ac Ad 9h 5s 2c".split())


def test_a_steel_wheel_is_a_straight_flush():
    assert cat("Ac 2c 3c 4c 5c") == STRAIGHT_FLUSH


def test_seven_cards_pick_the_best_five():
    # Three pair is not a hand: only the top two pairs play, with the best
    # remaining card as the kicker.
    rank = evaluate("Ad As 5h 5c 9d 9s 2c".split())
    assert rank == (TWO_PAIR, 12, 7, 3)
    assert evaluate("8c 8d 8h 3s 3c 2d 7h".split())[0] == FULL_HOUSE


def test_kickers_decide_when_the_pair_is_shared():
    better = evaluate("Ac Ad Kh 7s 3c".split())
    worse = evaluate("Ah As Qh 7d 3s".split())
    assert better > worse


def test_identical_hands_tie_exactly():
    assert evaluate("Ac Kd 9h 5s 2c".split()) == evaluate("Ah Ks 9d 5c 2h".split())


def test_the_best_five_are_the_ones_shown():
    shown = best_five("Ad As 5h 5c 9d 9s 2c".split())
    assert len(shown) == 5
    assert evaluate(shown) == evaluate("Ad As 5h 5c 9d 9s 2c".split())


def test_hands_are_described_the_way_the_ui_shows_them():
    assert describe("Ad As 5h 9c 2d".split()) == "PAIR (A)"
    assert describe("Kd Ks 7h 7c 2d".split()) == "TWO PAIR (K, 7)"
    assert describe("8c 8d 8h 3s 3c".split()) == "FULL HOUSE (8 over 3)"
    assert describe("9c 8d 7h 6s 5c".split()) == "STRAIGHT (9 high)"
    assert describe("Ah Jh 9h 5h 2h".split()) == "FLUSH (A high)"


# ------------------------------------------------- a second opinion


pokerkit = pytest.importorskip("pokerkit", reason="PokerKit is a dev-only check")


def test_agrees_with_pokerkit_over_random_showdowns():
    """Our evaluator is simple on purpose; this is what keeps it honest.

    A thousand random two-player showdowns, comparing only who wins — if the
    two disagree about a single board, something is wrong here.
    """
    from pokerkit import StandardHighHand

    rng = random.Random(20261024)  # fixed seed: this is a test, not a shuffle
    deck = make_deck()
    disagreements = []

    for _ in range(1000):
        cards = rng.sample(deck, 9)
        board, one, two = cards[:5], cards[5:7], cards[7:9]

        ours = (evaluate(one + board) > evaluate(two + board)) - (
            evaluate(one + board) < evaluate(two + board)
        )
        theirs_one = StandardHighHand.from_game("".join(one), "".join(board))
        theirs_two = StandardHighHand.from_game("".join(two), "".join(board))
        theirs = (theirs_one > theirs_two) - (theirs_one < theirs_two)

        if ours != theirs:
            disagreements.append((board, one, two, ours, theirs))

    assert not disagreements, disagreements[:3]
