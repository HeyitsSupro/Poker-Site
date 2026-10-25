"""How much a player is allowed to bet or raise.

Section 6.3 asks for limits that compose: the allowed range is the tightest
of everything currently switched on, so new limits (a flop cap, a turn cap)
can be added later without rewriting the betting code.

So each limit is a small function from the situation to a ceiling on the
raise-to amount, and the engine takes the lowest ceiling of the lot. Two
things are never limited away, because a player must always have something
legal to do:

* folding and calling;
* going all in for less than the minimum.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

NO_CEILING = float("inf")


@dataclass(frozen=True)
class Situation:
    """Everything a limit is allowed to look at."""

    street: str
    # Chips a player must match in total this street to stay in.
    current_bet: int
    # Everything in the middle: previous streets, this street, antes.
    pot_total: int
    # What this player still has to put in to call.
    to_call: int
    # What this player has already put in this street.
    committed: int
    # What this player has left in front of them.
    stack: int

    @property
    def all_in_to(self) -> int:
        """The raise-to amount that puts this player all in."""
        return self.committed + self.stack


Limit = Callable[[Situation], float]


def no_limit(_situation: Situation) -> float:
    return NO_CEILING


def pot_limit(situation: Situation) -> float:
    """Classic pot-limit: call first, then raise the size of the pot.

    Max raise-to = current bet + pot (everything already in, antes included)
    + the amount to call. With blinds 1/2 and no straddle, the first player
    to act preflop can raise to 2 + 3 + 2 = 7.
    """
    return situation.current_bet + situation.pot_total + situation.to_call


def preflop_cap(cap: int) -> Limit:
    """The house preflop limit: nobody may raise to more than `cap` preflop.

    A cap of 0 means off.
    """

    def limit(situation: Situation) -> float:
        if cap <= 0 or situation.street != "preflop":
            return NO_CEILING
        return cap

    return limit


def structure_limit(structure: str) -> Limit:
    if structure == "pot-limit":
        return pot_limit
    return no_limit


@dataclass(frozen=True)
class Range:
    """What a player may do about the current bet."""

    can_raise: bool
    # The smallest legal raise-to, when a raise is possible at all.
    min_raise_to: int
    # The largest legal raise-to. Equals min_raise_to when a limit squeezes
    # the range to a single amount.
    max_raise_to: int
    # True when the only way to put more in is to go all in for less than a
    # legal raise. Always allowed.
    all_in_only: bool
    # Why the top of the range is where it is, for the UI to explain a cap.
    capped_by: str | None = None


def allowed_range(
    situation: Situation,
    min_raise_increment: int,
    limits: list[tuple[str, Limit]],
) -> Range:
    """Work out the legal raise-to range, tightest limit wins.

    `min_raise_increment` is the size of the last full bet or raise — the
    standard minimum-raise rule.
    """
    all_in_to = situation.all_in_to
    unlimited_min = situation.current_bet + min_raise_increment

    ceiling: float = all_in_to
    capped_by: str | None = None
    for name, limit in limits:
        value = limit(situation)
        if value < ceiling:
            ceiling = value
            capped_by = name

    max_raise_to = int(min(all_in_to, ceiling))

    if all_in_to <= situation.current_bet:
        # Cannot even cover the call: the only move with chips is all in.
        return Range(False, 0, 0, all_in_only=True, capped_by=capped_by)

    if max_raise_to < unlimited_min:
        # The ceiling (a limit, or the player's own stack) sits below a full
        # raise. Going all in for less is still allowed; a normal raise is
        # not.
        if all_in_to > situation.current_bet and max_raise_to >= all_in_to:
            return Range(
                True, all_in_to, all_in_to, all_in_only=True, capped_by=capped_by
            )
        return Range(False, 0, 0, all_in_only=True, capped_by=capped_by)

    return Range(
        True,
        min(unlimited_min, max_raise_to),
        max_raise_to,
        all_in_only=False,
        capped_by=capped_by,
    )
