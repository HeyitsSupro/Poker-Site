"""Hand evaluation: rank any 5-to-7 card holding, and say what it is.

`evaluate` returns a tuple that sorts the way poker hands compare, so two
hands are compared with `<`, equal tuples are a genuine tie (a split pot),
and nothing anywhere needs to know how the tuple is built.

The implementation is the straightforward one — count the ranks, look for a
flush, look for a straight — chosen because it is easy to read and to check
against a second opinion. The test suite plays it off against PokerKit over
random boards, so "easy to read" does not mean "taken on trust".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import combinations

from .cards import RANK_NAMES, RANKS, rank_of, suit_of

HIGH_CARD = 0
PAIR = 1
TWO_PAIR = 2
TRIPS = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
QUADS = 7
STRAIGHT_FLUSH = 8

CATEGORY_NAMES = {
    HIGH_CARD: "HIGH CARD",
    PAIR: "PAIR",
    TWO_PAIR: "TWO PAIR",
    TRIPS: "THREE OF A KIND",
    STRAIGHT: "STRAIGHT",
    FLUSH: "FLUSH",
    FULL_HOUSE: "FULL HOUSE",
    QUADS: "FOUR OF A KIND",
    STRAIGHT_FLUSH: "STRAIGHT FLUSH",
}

# The wheel: an ace playing low under 2-3-4-5.
_WHEEL = frozenset({12, 0, 1, 2, 3})


def _straight_high(ranks) -> int | None:
    """Highest card of the best straight in `ranks`, or None.

    The wheel (A-2-3-4-5) counts, with the ace playing low, so its high card
    is the five.
    """
    present = set(ranks)
    for high in range(12, 2, -1):
        if all(high - offset in present for offset in range(5)):
            return high
    if _WHEEL <= present:
        return 3
    return None


def evaluate(cards: Sequence[str]) -> tuple:
    """Rank a holding of five to seven cards. Bigger tuple wins."""
    if len(cards) < 5:
        raise ValueError("need at least five cards to evaluate")

    ranks = []
    by_suit: dict[str, list[int]] = {}
    for card in cards:
        rank = rank_of(card)
        ranks.append(rank)
        by_suit.setdefault(suit_of(card), []).append(rank)

    # With seven cards at most one suit can reach five, so the first is the
    # only one.
    flush_suit = next((s for s, rs in by_suit.items() if len(rs) >= 5), None)
    if flush_suit is not None:
        flush_ranks = by_suit[flush_suit]
        high = _straight_high(flush_ranks)
        if high is not None:
            return (STRAIGHT_FLUSH, high)
        return (FLUSH, *sorted(flush_ranks, reverse=True)[:5])

    counts = Counter(ranks)
    # Most of a rank first, then highest rank: the usual reading order.
    grouped = sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True)
    shape = [count for _rank, count in grouped]

    if shape[0] == 4:
        quad = grouped[0][0]
        kicker = max(r for r in ranks if r != quad)
        return (QUADS, quad, kicker)

    if shape[0] == 3 and len(shape) > 1 and shape[1] >= 2:
        return (FULL_HOUSE, grouped[0][0], grouped[1][0])

    high = _straight_high(ranks)
    if high is not None:
        return (STRAIGHT, high)

    if shape[0] == 3:
        best = grouped[0][0]
        kickers = sorted((r for r in ranks if r != best), reverse=True)[:2]
        return (TRIPS, best, *kickers)

    if shape[0] == 2 and shape[1] == 2:
        # Three pairs is possible with seven cards; the lowest pair drops and
        # its higher card can still play as the kicker.
        pairs = [rank for rank, count in grouped if count == 2][:2]
        kicker = max(r for r in ranks if r not in pairs)
        return (TWO_PAIR, pairs[0], pairs[1], kicker)

    if shape[0] == 2:
        pair = grouped[0][0]
        kickers = sorted((r for r in ranks if r != pair), reverse=True)[:3]
        return (PAIR, pair, *kickers)

    return (HIGH_CARD, *sorted(ranks, reverse=True)[:5])


def best_five(cards: Sequence[str]) -> list[str]:
    """The five cards that make the hand, for showing at showdown."""
    if len(cards) <= 5:
        return list(cards)
    return list(max(combinations(cards, 5), key=evaluate))


def describe(cards: Sequence[str]) -> str:
    """The hand in words, as shown under a player's own cards.

    e.g. "PAIR (A)", "TWO PAIR (K, 7)", "FLUSH (A high)".
    """
    category, *rest = evaluate(cards)
    name = CATEGORY_NAMES[category]

    def label(index: int) -> str:
        return RANK_NAMES[RANKS[index]]

    if category in (PAIR, TRIPS, QUADS, HIGH_CARD):
        return f"{name} ({label(rest[0])})"
    if category == TWO_PAIR:
        return f"{name} ({label(rest[0])}, {label(rest[1])})"
    if category == FULL_HOUSE:
        return f"{name} ({label(rest[0])} over {label(rest[1])})"
    # A straight or a flush is named by the card it runs to.
    return f"{name} ({label(rest[0])} high)"


def describe_any(cards: Sequence[str]) -> str | None:
    """Name a holding even before there are five cards to rank.

    Before the flop a player still wants to see what they have, so two cards
    are named directly rather than left blank until the board arrives.
    """
    if len(cards) >= 5:
        return describe(cards)
    if len(cards) < 2:
        return None

    ranks = sorted((rank_of(card) for card in cards), reverse=True)
    if len(set(ranks)) == 1:
        return f"{CATEGORY_NAMES[PAIR]} ({RANK_NAMES[RANKS[ranks[0]]]})"
    return f"{CATEGORY_NAMES[HIGH_CARD]} ({RANK_NAMES[RANKS[ranks[0]]]})"
