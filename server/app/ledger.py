"""The money bookkeeping (section 6.10).

Chips belong to the player, not the seat. Three things can happen to them:

* **Buying in** adds chips from outside and is the only thing that moves the
  buy-in total and the buy-in count.
* **Standing up** holds the player's stack on their record. It is out of
  play and cannot be lost. **Resuming** it puts it back and is *not* a
  buy-in: neither counter moves.
* **Cashing out** takes chips off the table and records the amount. If the
  player comes back for the same amount before the game ends, that is a
  reversal of the cash-out — again, not a new buy-in.

The arithmetic that has to hold at all times:

    net = cash-outs + held stack + seated stack - total buy-ins

and, apart from host adjustments which are logged separately, every player's
net adds up to zero across the table.
"""

from __future__ import annotations

import secrets
from typing import Any

from .identity import now_iso

# Entry kinds. Each one is appended, never edited.
BUY_IN = "buy_in"
CASH_OUT = "cash_out"
CASH_OUT_REVERSED = "cash_out_reversed"
RESUME_HELD = "resume_held"
STAND_UP = "stand_up"
HOST_ADJUSTMENT = "host_adjustment"


def entry(kind: str, player_id: str, amount: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": secrets.token_urlsafe(6),
        "kind": kind,
        "player_id": player_id,
        "amount": amount,
        "at": now_iso(),
        **extra,
    }


def record(state: dict, item: dict) -> None:
    state.setdefault("ledger", []).append(item)


def buy_in(state: dict, player: dict, amount: int) -> dict:
    player["buy_in_total"] += amount
    player["buy_in_count"] += 1
    if player["buy_in_count"] > 1:
        player["rebuys"] += 1
    item = entry(BUY_IN, player["id"], amount, count=player["buy_in_count"])
    record(state, item)
    return item


def hold(state: dict, player: dict, amount: int) -> dict:
    """Stand up: the stack comes off the table and sits on the record."""
    player["held"] += amount
    item = entry(STAND_UP, player["id"], amount, held=player["held"])
    record(state, item)
    return item


def resume_held(state: dict, player: dict, amount: int) -> dict:
    """Sit back down with your own chips. Not a buy-in."""
    player["held"] -= amount
    item = entry(RESUME_HELD, player["id"], amount, held=player["held"])
    record(state, item)
    return item


def cash_out(state: dict, player: dict, amount: int) -> dict:
    player["cash_out_total"] += amount
    item = entry(CASH_OUT, player["id"], amount)
    record(state, item)
    return item


def open_cash_outs(state: dict, player_id: str) -> list[dict]:
    """Cash-outs that have not been reversed, newest first."""
    reversed_ids = {
        item.get("reverses")
        for item in state.get("ledger", [])
        if item["kind"] == CASH_OUT_REVERSED
    }
    return [
        item
        for item in reversed(state.get("ledger", []))
        if item["kind"] == CASH_OUT
        and item["player_id"] == player_id
        and item["id"] not in reversed_ids
    ]


def reverse_cash_out(state: dict, player: dict, original: dict) -> dict:
    """Come back with exactly what you cashed out.

    Modelled as its own entry pointing at the original rather than by
    deleting anything, so the log still shows what happened and in what
    order.
    """
    player["cash_out_total"] -= original["amount"]
    item = entry(
        CASH_OUT_REVERSED,
        player["id"],
        original["amount"],
        reverses=original["id"],
    )
    record(state, item)
    return item


def host_adjustment(
    state: dict, player: dict, delta: int, by: str, reason: str | None
) -> dict:
    item = entry(
        HOST_ADJUSTMENT, player["id"], delta, by=by, reason=reason or ""
    )
    record(state, item)
    return item


def seated_stack(state: dict, player_id: str) -> int:
    for seat in state.get("seats", {}).values():
        if seat.get("player_id") == player_id:
            return seat.get("stack", 0)
    return 0


def summary_for(state: dict, player_id: str) -> dict[str, Any]:
    player = state["players"][player_id]
    seated = seated_stack(state, player_id)
    adjustments = sum(
        item["amount"]
        for item in state.get("ledger", [])
        if item["kind"] == HOST_ADJUSTMENT and item["player_id"] == player_id
    )
    net = (
        player["cash_out_total"]
        + player["held"]
        + seated
        - player["buy_in_total"]
    )
    return {
        "player_id": player_id,
        "nick": player["nick"],
        "buy_in_total": player["buy_in_total"],
        "buy_in_count": player["buy_in_count"],
        "rebuys": player["rebuys"],
        "cash_out_total": player["cash_out_total"],
        "held": player["held"],
        "seated": seated,
        "host_adjustments": adjustments,
        "net": net,
    }


def summary(state: dict) -> list[dict[str, Any]]:
    return [summary_for(state, pid) for pid in state.get("players", {})]


def tracker_rows(state: dict) -> list[dict[str, Any]]:
    """The session summary in the owner's own tracker format.

    One row per player, the columns being buy-in total, number of buy-ins,
    profit or loss, and a notes column left empty for the owner to fill in
    afterwards:

        | <buy-in> | <count> | <pnl> | <notes> |
    """
    rows = []
    for row in summary(state):
        rows.append(
            {
                "nick": row["nick"],
                "buy_in": row["buy_in_total"],
                # Resuming a held stack is not a buy-in, so this is the
                # count the ledger kept, not a count of times they sat.
                "count": row["buy_in_count"],
                "pnl": row["net"],
                "notes": "",
            }
        )
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


def unclaimed_in_the_middle(state: dict) -> int:
    """Chips in the pot that no player's net covers yet.

    When someone folds and leaves mid-hand they take what is in front of
    them, and what they had already bet stays in the pot waiting to be won.
    For those few minutes it belongs to nobody, so the nets would look short
    by exactly that much. This is that amount.
    """
    hand = state.get("hand")
    if not hand or hand.get("street") == "complete":
        return 0
    orphaned = 0
    for number, seat in hand.get("seats", {}).items():
        row = state.get("seats", {}).get(str(number))
        if not row or row.get("player_id") != seat.get("player_id"):
            orphaned += seat.get("total", 0)
    return orphaned


def nets_balance(state: dict) -> bool:
    """Every chip is accounted for: the nets sum to zero.

    Host adjustments create chips on purpose, so they are excluded — they
    are the one logged exception section 6.10 allows. Chips still in flight
    in the middle are added back, so this holds during a hand as well as
    between them.
    """
    total = sum(row["net"] - row["host_adjustments"] for row in summary(state))
    return total + unclaimed_in_the_middle(state) == 0
