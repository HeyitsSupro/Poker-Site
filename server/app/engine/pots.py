"""Main pot, side pots, uncalled bets and odd chips.

The conventions here are the ones casinos and online rooms use (section 6.9
says to follow them and to write down which they are):

* **Uncalled bets come back.** Whatever one player put in beyond what anyone
  else could match was never contested, so it is returned before any pot is
  built. This is what makes "everyone folds" and "all-in for more than the
  caller has" come out right.
* **Side pots are built in layers.** Each distinct commitment level makes a
  pot; a player is eligible for a layer only if they reached it and did not
  fold. Chips from folded players stay in the layer they reached.
* **Odd chips go left of the button.** When a pot does not divide evenly, the
  spare chips go one at a time to the tied players in clockwise order
  starting from the first seat left of the button — so the earliest position
  gets the first spare.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pot:
    amount: int
    # Seats that may win this layer: they reached it and did not fold.
    eligible: frozenset[int]


@dataclass
class Settlement:
    """Who gets what, and why."""

    refunds: dict[int, int] = field(default_factory=dict)
    awards: dict[int, int] = field(default_factory=dict)
    pots: list[Pot] = field(default_factory=list)
    # One entry per pot: {"amount", "winners", "eligible"} for the log.
    detail: list[dict] = field(default_factory=list)

    def credit(self, seat: int, amount: int) -> None:
        if amount:
            self.awards[seat] = self.awards.get(seat, 0) + amount


def return_uncalled(contributions: Mapping[int, int]) -> tuple[dict[int, int], dict[int, int]]:
    """Split contributions into (contested, refunded).

    If one player put in more than every other player, the excess was never
    called and is theirs again.
    """
    contested = dict(contributions)
    refunds: dict[int, int] = {}
    if len(contested) < 2:
        for seat, amount in contested.items():
            if amount:
                refunds[seat] = amount
                contested[seat] = 0
        return contested, refunds

    ordered = sorted(contested.items(), key=lambda kv: kv[1], reverse=True)
    (top_seat, top), (_, second) = ordered[0], ordered[1]
    if top > second:
        refunds[top_seat] = top - second
        contested[top_seat] = second
    return contested, refunds


def build_pots(
    contributions: Mapping[int, int], contenders: Iterable[int]
) -> list[Pot]:
    """Layer the contested chips into a main pot and any side pots."""
    contenders = set(contenders)
    levels = sorted({amount for amount in contributions.values() if amount > 0})
    pots: list[Pot] = []
    previous = 0

    for level in levels:
        amount = sum(
            min(committed, level) - min(committed, previous)
            for committed in contributions.values()
        )
        eligible = frozenset(
            seat for seat in contenders if contributions.get(seat, 0) >= level
        )
        previous = level
        if amount <= 0:
            continue
        if not eligible:
            # Nobody left can win this layer (every contributor folded). Fold
            # it into the layer below rather than letting chips vanish.
            if pots:
                last = pots[-1]
                pots[-1] = Pot(last.amount + amount, last.eligible)
            continue
        if pots and pots[-1].eligible == eligible:
            # Same set of contenders: one pot, not two.
            pots[-1] = Pot(pots[-1].amount + amount, eligible)
        else:
            pots.append(Pot(amount, eligible))

    return pots


def share_out(
    pot: Pot, winners: list[int], order: list[int]
) -> dict[int, int]:
    """Split one pot between tied winners, odd chips left of the button.

    `order` is the seating order starting at the first seat left of the
    button, which is the order the spare chips are handed out in.
    """
    if not winners:
        return {}
    share, odd = divmod(pot.amount, len(winners))
    payout = {seat: share for seat in winners}
    queue = [seat for seat in order if seat in winners]
    for index in range(odd):
        payout[queue[index % len(queue)]] += 1
    return payout


def settle(
    contributions: Mapping[int, int],
    contenders: Iterable[int],
    rank_of_seat,
    order: list[int],
) -> Settlement:
    """Work out every payout for a finished hand.

    `rank_of_seat(seat)` returns a comparable hand rank (bigger wins); it is
    only ever called for seats that are still in the hand.
    `order` is the seating order from the first seat left of the button.
    """
    contested, refunds = return_uncalled(contributions)
    result = Settlement(refunds=dict(refunds))
    for seat, amount in refunds.items():
        result.credit(seat, amount)

    result.pots = build_pots(contested, contenders)

    for pot in result.pots:
        ranked = {seat: rank_of_seat(seat) for seat in pot.eligible}
        best = max(ranked.values())
        winners = [seat for seat, rank in ranked.items() if rank == best]
        payout = share_out(pot, winners, order)
        for seat, amount in payout.items():
            result.credit(seat, amount)
        result.detail.append(
            {
                "amount": pot.amount,
                "winners": sorted(winners),
                "eligible": sorted(pot.eligible),
                "payout": {str(s): a for s, a in sorted(payout.items())},
            }
        )

    return result
