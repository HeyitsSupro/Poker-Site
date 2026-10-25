"""Host controls (section 7, "Host controls").

The host is whoever made the table. They can hand the role to a co-host, and
the host code lets a trusted friend take over if the host changes phone or
drops out of the game entirely.

Everything structural — blinds, antes, the betting structure, the house
limits, seat count, buy-in range — takes effect from the next hand, never in
the middle of one. Stack edits are the same, and are always logged with who
made them and why, because a stack edit is the one thing that can create
chips out of nothing.
"""

from __future__ import annotations

from typing import Any

from . import identity, ledger
from .table import (
    AWAY,
    PLAYING,
    Rejected,
    State,
    _clone,
    _load_hand,
    _release_seat,
    _require_player,
    _settle_table,
    note,
    seat_of,
)
from .protocol import clean_text
from .engine import holdem

# Enough for the handful of games a group actually plays, not a filing
# cabinet that bloats every saved state.
MAX_PRESETS = 12
from .store import Event

# What the host may change, and how each value is checked. Anything not in
# here cannot be set at all, so a stray key in a message can never become a
# table setting.
SETTING_RULES: dict[str, dict[str, Any]] = {
    "small_blind": {"type": int, "min": 1, "max": 1_000_000},
    "big_blind": {"type": int, "min": 1, "max": 2_000_000},
    "ante": {"type": int, "min": 0, "max": 1_000_000},
    "ante_type": {"type": str, "choices": {"none", "all", "big-blind"}},
    "structure": {"type": str, "choices": {"no-limit", "pot-limit"}},
    "preflop_limit": {"type": int, "min": 0, "max": 100_000_000},
    "max_straddle": {"type": int, "min": 0, "max": 100_000_000},
    "max_runs": {"type": int, "min": 1, "max": 4},
    "run_timer_seconds": {"type": int, "min": 3, "max": 60},
    "rabbit_hunting": {"type": bool},
    "action_seconds": {"type": int, "min": 0, "max": 600},
    "time_bank_seconds": {"type": int, "min": 0, "max": 600},
    "showdown_seconds": {"type": int, "min": 0, "max": 120},
    "between_hands_seconds": {"type": int, "min": 0, "max": 120},
    "seat_count": {"type": int, "min": 2, "max": 10},
    "buy_in": {"type": int, "min": 1, "max": 100_000_000},
    "min_buy_in": {"type": int, "min": 1, "max": 100_000_000},
    "max_buy_in": {"type": int, "min": 1, "max": 100_000_000},
    "fixed_buy_in": {"type": int, "min": 0, "max": 100_000_000},
    "resume_after_cash_out": {
        "type": str,
        "choices": {"allowed", "approval", "off"},
    },
    "auto_approve_buy_ins": {"type": bool},
    "rathole_minutes": {"type": int, "min": 0, "max": 600},
}


def _require_host(state: State, by: str) -> dict:
    if not identity.is_host(state, by):
        raise Rejected("Only the host can do that.")
    return state["players"][by]


def _clean_setting(key: str, value: Any) -> Any:
    rule = SETTING_RULES.get(key)
    if rule is None:
        raise Rejected(f"{key.replace('_', ' ')} is not a setting you can change.")

    if rule["type"] is bool:
        return bool(value)
    if rule["type"] is str:
        text = str(value)
        if text not in rule["choices"]:
            raise Rejected(f"{text!r} is not a valid {key.replace('_', ' ')}.")
        return text

    try:
        number = int(value)
    except (TypeError, ValueError):
        raise Rejected(f"{key.replace('_', ' ')} must be a number.") from None
    if not rule["min"] <= number <= rule["max"]:
        raise Rejected(
            f"{key.replace('_', ' ')} must be between {rule['min']} and {rule['max']}."
        )
    return number


def change_settings(by: str, changes: dict[str, Any]):
    """Apply host settings. They take effect from the next hand."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        if not changes:
            raise Rejected("Nothing to change.")

        cleaned = {key: _clean_setting(key, value) for key, value in changes.items()}
        merged = {**state["settings"], **cleaned}

        if merged["small_blind"] > merged["big_blind"]:
            raise Rejected("The small blind cannot be bigger than the big blind.")
        if merged["min_buy_in"] > merged["max_buy_in"]:
            raise Rejected("The smallest buy-in cannot be bigger than the largest.")
        if merged["max_straddle"] and merged["max_straddle"] < merged["big_blind"] * 2:
            raise Rejected("A straddle has to be at least twice the big blind.")
        occupied = [int(n) for n in state["seats"]]
        if occupied and merged["seat_count"] <= max(occupied):
            raise Rejected(
                "Someone is sitting in a seat that would disappear. Ask them to "
                "move first."
            )

        state["settings"] = merged
        note(state, "settings_changed", by=by, changes=cleaned)
        return _settle_table(
            state, [Event("settings_changed", {"changes": cleaned}, by)]
        )

    return mutate


def save_preset(by: str, name: str):
    """Keep the current settings under a name, to load at the next game."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        label = clean_text(name, 30)
        if not label:
            raise Rejected("Give the preset a name.")
        presets = dict(state.get("presets", {}))
        if len(presets) >= MAX_PRESETS and label not in presets:
            raise Rejected(f"You can keep {MAX_PRESETS} presets. Delete one first.")
        # Only the rules are saved; seat count and buy-ins travel with them
        # because the host usually wants the whole shape of the game back.
        presets[label] = dict(state["settings"])
        state["presets"] = presets
        note(state, "preset_saved", by=by, name=label)
        return state, [Event("preset_saved", {"name": label}, by)]

    return mutate


def load_preset(by: str, name: str):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        preset = (state.get("presets") or {}).get(name)
        if preset is None:
            raise Rejected("There is no preset by that name.")
        # Go through the usual path so every value is checked again: a
        # preset saved under older rules must not slip past validation.
        # Keys this version no longer knows about are dropped rather than
        # refused, so an old preset still loads.
        wanted = {k: v for k, v in preset.items() if k in SETTING_RULES}
        return change_settings(by, wanted)(state)

    return mutate


def delete_preset(by: str, name: str):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        presets = dict(state.get("presets", {}))
        if presets.pop(name, None) is None:
            raise Rejected("There is no preset by that name.")
        state["presets"] = presets
        note(state, "preset_deleted", by=by, name=name)
        return state, [Event("preset_deleted", {"name": name}, by)]

    return mutate


def edit_stack(by: str, player_id: str, amount: int, reason: str | None):
    """Set a player's stack. Between hands only, and always logged.

    This is the one action that can create or destroy chips, which is why it
    is recorded as its own ledger entry with who did it and why, and why the
    ledger reports host adjustments separately from everyone's net.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        player = _require_player(state, player_id)

        hand = _load_hand(state)
        if hand is not None and hand.street != holdem.COMPLETE:
            raise Rejected("Stacks can only be edited between hands.")

        seat_number = seat_of(state, player_id)
        if seat_number is None:
            raise Rejected("That player is not sitting down.")
        if amount < 0:
            raise Rejected("A stack cannot be negative.")

        row = state["seats"][str(seat_number)]
        before = row.get("stack", 0)
        delta = amount - before
        if delta == 0:
            raise Rejected("That is already their stack.")

        row["stack"] = amount
        ledger.host_adjustment(state, player, delta, by, reason)
        note(
            state,
            "stack_edited",
            by=by,
            player_id=player_id,
            before=before,
            after=amount,
            reason=reason or "",
        )
        return _settle_table(
            state,
            [
                Event(
                    "stack_edited",
                    {
                        "player_id": player_id,
                        "before": before,
                        "after": amount,
                        "reason": reason or "",
                    },
                    by,
                )
            ],
        )

    return mutate


def set_co_host(by: str, player_id: str, is_co_host: bool):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        player = _require_player(state, player_id)
        player["is_co_host"] = bool(is_co_host)
        note(state, "co_host", by=by, player_id=player_id, on=bool(is_co_host))
        return state, [
            Event("co_host", {"player_id": player_id, "on": bool(is_co_host)}, by)
        ]

    return mutate


def claim_host(player_id: str, code: str):
    """Take over as host using the host code.

    The code is what makes the role recoverable: the host changes phone, or
    goes home early, and someone they trust can still run the game.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        if not identity.host_code_matches(state, code):
            raise Rejected("That host code is not right.")
        for other in state["players"].values():
            other["is_host"] = False
        player["is_host"] = True
        note(state, "host_changed", player_id=player_id)
        return state, [Event("host_changed", {}, player_id)]

    return mutate


def remove_player(by: str, player_id: str, revoke: bool = False):
    """Take someone out of their seat, and optionally bar them.

    Their chips are not touched: they go to the held stack on their record,
    the same as standing up, so the ledger still balances.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        player = state["players"].get(player_id)
        if player is None:
            raise Rejected("That player is not at this table.")

        seat_number = seat_of(state, player_id)
        events: list[Event] = []
        if seat_number is not None:
            hand = _load_hand(state)
            in_live_hand = (
                hand is not None
                and hand.street != holdem.COMPLETE
                and seat_number in hand.seats
                and not hand.seats[seat_number].folded
            )
            if in_live_hand:
                # Do not snatch a live hand away: they leave when it ends.
                state["seats"][str(seat_number)]["leaving"] = True
                note(state, "removal_pending", by=by, player_id=player_id)
            else:
                events += _release_seat(state, seat_number, player_id)

        if revoke:
            player["revoked"] = True
            note(state, "player_revoked", by=by, player_id=player_id)
            events.append(Event("player_revoked", {"player_id": player_id}, by))
        else:
            note(state, "player_removed", by=by, player_id=player_id)
            events.append(Event("player_removed", {"player_id": player_id}, by))

        return _settle_table(state, events)

    return mutate


def set_player_away(by: str, player_id: str, away: bool):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        seat_number = seat_of(state, player_id)
        if seat_number is None:
            raise Rejected("That player is not sitting down.")
        state["seats"][str(seat_number)]["state"] = AWAY if away else PLAYING
        note(state, "host_set_away", by=by, player_id=player_id, away=away)
        return _settle_table(
            state, [Event("host_set_away", {"player_id": player_id, "away": away}, by)]
        )

    return mutate


def end_game(by: str):
    """Close the game: every stack is cashed out so the ledger is final."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        hand = _load_hand(state)
        if hand is not None and hand.street != holdem.COMPLETE:
            raise Rejected("Finish the hand first, then end the game.")

        events: list[Event] = []
        for number, row in list(state["seats"].items()):
            events += _release_seat(state, int(number), row["player_id"])

        for player_id, player in state["players"].items():
            amount = player["held"]
            if amount > 0:
                player["held"] = 0
                ledger.cash_out(state, player, amount)
                events.append(Event("cash_out", {"amount": amount}, player_id))

        state["ended"] = True
        state["paused"] = True
        note(state, "game_ended", by=by)
        events.append(Event("game_ended", {}, by))
        return state, events

    return mutate


# ----------------------------------------------------- buy-in approvals


def request_buy_in(player_id: str, seat_number: int, amount: int, request_id: str):
    """Ask the host for a seat when auto-approve is off."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        state.setdefault("pending_buy_ins", [])
        state["pending_buy_ins"] = [
            r for r in state["pending_buy_ins"] if r["id"] != request_id
        ] + [
            {
                "id": request_id,
                "player_id": player_id,
                "seat": seat_number,
                "amount": amount,
                "at": identity.now_iso(),
            }
        ]
        note(state, "buy_in_requested", player_id=player_id, amount=amount)
        return state, [Event("buy_in_requested", {"amount": amount}, player_id)]

    return mutate


def resolve_buy_in(by: str, request_id: str, approve: bool):
    from .table import sit  # imported here to avoid a cycle at module load

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_host(state, by)
        pending = state.get("pending_buy_ins", [])
        request = next((r for r in pending if r["id"] == request_id), None)
        if request is None:
            raise Rejected("That request is no longer waiting.")

        state["pending_buy_ins"] = [r for r in pending if r["id"] != request_id]
        if not approve:
            note(state, "buy_in_denied", by=by, player_id=request["player_id"])
            return state, [
                Event("buy_in_denied", {"player_id": request["player_id"]}, by)
            ]

        note(state, "buy_in_approved", by=by, player_id=request["player_id"])
        state, events = sit(
            request["player_id"], request["seat"], request["amount"], resume=False
        )(state)
        return state, [
            Event("buy_in_approved", {"player_id": request["player_id"]}, by),
            *events,
        ]

    return mutate


# ---------------------------------------------------------- straddling


def post_straddle(player_id: str, amount: int):
    """Put up a straddle for the next hand.

    Only from under the gun, only up to the host's maximum, and only when
    the host has switched straddling on at all (a maximum of 0 means off).
    """

    wanted = int(amount)

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        settings = state["settings"]
        maximum = settings.get("max_straddle", 0)
        if maximum <= 0:
            raise Rejected("Straddling is switched off at this table.")

        minimum = settings["big_blind"] * 2
        if wanted < minimum or wanted > maximum:
            raise Rejected(f"A straddle here is between {minimum} and {maximum}.")

        seat_number = seat_of(state, player_id)
        if seat_number is None:
            raise Rejected("You are not sitting down.")

        state["next_straddle"] = {"seat": seat_number, "amount": wanted}
        note(state, "straddle_posted", player_id=player_id, amount=wanted)
        return state, [Event("straddle_posted", {"amount": wanted}, player_id)]

    return mutate
