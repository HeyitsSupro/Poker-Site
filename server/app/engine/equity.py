"""Win percentages while players are all in.

Section 6.6 wants each player's chance of winning (and of tying) shown and
updated as the cards come out. Two rules shape the implementation:

* It is computed on the server. The client is never told anything it could
  work the answer out from.
* It uses **only cards that are already face up to everyone** — the known
  hole cards of players who are all in and shown, and the board so far.
  Nothing in here ever sees the rest of the deck, so there is nothing to
  leak even by accident.

How it is worked out depends on how much is left to come, because the honest
answer is cheap in most cases:

    cards to come   possibilities   method
    0               1               just evaluate
    1               ~45             every one of them
    2               ~1,000          every one of them
    3+              too many        a random sample, big enough to be steady

So from the flop on the numbers are exact. Before the flop they are a
sample, which is the usual trade-off every poker site makes.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from itertools import combinations

from .cards import DECK
from .evaluator import evaluate

# Above this many possible runouts, sample instead of enumerating.
EXACT_LIMIT = 2_000
SAMPLES = 4_000


def _choose(n: int, k: int) -> int:
    result = 1
    for index in range(k):
        result = result * (n - index) // (index + 1)
    return result


def equities(
    hands: dict[int, Sequence[str]],
    board: Sequence[str],
    *,
    board_size: int = 5,
    samples: int = SAMPLES,
) -> dict[int, dict[str, float]]:
    """Each player's chance of winning and of tying, as percentages.

    `hands` maps a seat to its two face-up cards. Seats that are not shown
    must not be passed in — their cards are not public, so they cannot be
    part of a public number.
    """
    if len(hands) < 2:
        return {seat: {"win": 100.0, "tie": 0.0} for seat in hands}

    board = list(board)
    known = set(board)
    for cards in hands.values():
        known.update(cards)
    deck = [card for card in DECK if card not in known]
    to_come = max(0, board_size - len(board))

    wins = {seat: 0.0 for seat in hands}
    ties = {seat: 0.0 for seat in hands}
    trials = 0

    def score(runout: Sequence[str]) -> None:
        nonlocal trials
        trials += 1
        full = board + list(runout)
        ranked = {seat: evaluate(list(cards) + full) for seat, cards in hands.items()}
        best = max(ranked.values())
        winners = [seat for seat, rank in ranked.items() if rank == best]
        if len(winners) == 1:
            wins[winners[0]] += 1
        else:
            # A split counts as a tie for everyone in it, and as a share of
            # the pot rather than a win.
            for seat in winners:
                ties[seat] += 1

    if to_come == 0:
        score(())
    elif _choose(len(deck), to_come) <= EXACT_LIMIT:
        for runout in combinations(deck, to_come):
            score(runout)
    else:
        rng = secrets.SystemRandom()
        for _ in range(samples):
            score(rng.sample(deck, to_come))

    return {
        seat: {
            "win": round(100.0 * wins[seat] / trials, 1),
            "tie": round(100.0 * ties[seat] / trials, 1),
        }
        for seat in hands
    }
