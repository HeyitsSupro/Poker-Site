"""The table: seats, the hand lifecycle, and what each person is shown.

This sits between the pure engine and the server. The engine knows about one
hand; this knows about a table that outlives hands — who is sitting where,
whose chips are whose, when the next hand starts, and which blinds it starts
with.

Everything here is the same shape as phase 0: state is a plain dict, and
every change is a pure function `state -> (state, events)` that the store
commits in one transaction. Nothing reaches for the network, the database or
the clock; the time is passed in.

The one place hidden information is decided is `view_for`. Hole cards leave
it only for the player they belong to, or at showdown for a hand that was
turned face up. The deck never leaves at all.
"""

from __future__ import annotations

import copy
import time
from datetime import datetime
from typing import Any

from . import clock, identity, ledger
from .engine import equity
from .config import STATE_FORMAT_VERSION
from .engine import holdem
from .engine.evaluator import describe_any
from .protocol import MAX_CHAT_LENGTH, MAX_NICK_LENGTH, clean_text
from .store import Event

State = dict[str, Any]

MAX_LOG = 200
MAX_MESSAGES = 60

# Emoji reactions: a small fixed set, so nothing arbitrary is rendered next
# to a seat and the client can ship the artwork it needs.
REACTIONS = ("👍", "😂", "😮", "😍", "😡", "🤔", "🎉", "💀")
REACTION_SECONDS = 5
MAX_REACTIONS = 12
MAX_NOTE_LENGTH = 200

# Seat states.
PLAYING = "playing"      # in the current hand, or ready for the next
WAITING = "waiting"      # sat down mid-hand: "IN NEXT HAND"
AWAY = "away"            # sitting out, keeps the seat

# Section 13: the defaults the host can change in settings.
DEFAULT_SETTINGS: dict[str, Any] = {
    "seat_count": 10,
    "small_blind": 1,
    "big_blind": 2,
    "ante": 0,
    "ante_type": "none",
    "structure": "no-limit",
    "preflop_limit": 0,
    "max_straddle": 0,
    "max_runs": 3,
    "run_timer_seconds": 10,
    "rabbit_hunting": True,
    "action_seconds": 30,
    "time_bank_seconds": 60,
    "showdown_seconds": 10,
    # The pause after a hand is paid out, before the next is dealt. It is
    # what makes rabbit hunting possible at all (section 6.7 allows it only
    # between hands) and gives everyone a moment to see the winner.
    "between_hands_seconds": 6,
    "buy_in": 200,
    "min_buy_in": 40,
    "max_buy_in": 400,
    "fixed_buy_in": 0,
    "resume_after_cash_out": "allowed",
    "auto_approve_buy_ins": True,
    # Cannot buy back in for less than you left with, for this many minutes.
    # 0 is off.
    "rathole_minutes": 0,
}


class Rejected(ValueError):
    """An action the player is not allowed to take. The message is shown."""


# --------------------------------------------------------------- new table


def new_state(name: str = "Home game", settings: dict | None = None) -> State:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings or {})
    return {
        "v": STATE_FORMAT_VERSION,
        "name": clean_text(name, 40, "Home game"),
        "created_at": identity.now_iso(),
        "settings": merged,
        "players": {},
        "seats": {},
        "ledger": [],
        "messages": [],
        "log": [],
        "hand": None,
        "hand_no": 0,
        "button": None,
        "last_bb_seat": None,
        "paused": False,
        "ended": False,
        "host_code_hash": None,
        "pending_claims": [],
        "pending_buy_ins": [],
        "last_sb_seat": None,
        "last_hand": None,
        "next_straddle": None,
        # At most one countdown is running at a time (see clock.py).
        "deadline": None,
        # Set while a finished hand waits for show-or-muck decisions.
        "showdown": None,
        "equity": None,
        # Players queued for a seat, or for a particular seat.
        "waitlist": [],
        # Host-saved bundles of settings, by name.
        "presets": {},
        "between_hands_until": None,
        # Emoji beside a seat, and rabbit-hunt reveals, both of which belong
        # to the gap between hands rather than to any hand.
        "reactions": [],
    }


def note(state: State, kind: str, **payload: Any) -> None:
    """Append to the table log the whole table can read."""
    entry = {"kind": kind, "at": identity.now_iso(), **payload}
    state["log"] = (state.get("log", []) + [entry])[-MAX_LOG:]


def _clone(state: State) -> State:
    """Each mutate works on its own copy, so a rejection changes nothing."""
    return copy.deepcopy(state)


# ----------------------------------------------------------------- seating


def seat_of(state: State, player_id: str) -> int | None:
    for number, seat in state["seats"].items():
        if seat.get("player_id") == player_id:
            return int(number)
    return None


def occupied(state: State) -> dict[int, dict]:
    return {int(n): s for n, s in state["seats"].items() if s.get("player_id")}


def chips_owned(state: State) -> int:
    """Every chip in the game, wherever it is sitting right now.

    This should always equal the sum of everyone's buy-ins, and the fuzz
    suite checks exactly that after every single move. Getting it right
    means counting four places, because a seat's `stack` is ownership
    between hands but not during one:

      * what is in front of each player at this moment;
      * the middle, which includes chips from players who have since folded
        and left the table;
      * stacks held off the table by people who stood up;
      * chips cashed out.
    """
    hand = _load_hand(state)
    live = hand is not None and hand.street != holdem.COMPLETE

    total = sum(
        player["held"] + player["cash_out_total"]
        for player in state["players"].values()
    )
    for number, row in state["seats"].items():
        seat_number = int(number)
        in_hand = (
            live
            and seat_number in hand.seats
            and hand.seats[seat_number].player_id == row.get("player_id")
        )
        total += hand.seats[seat_number].stack if in_hand else row.get("stack", 0)

    if live:
        total += hand.pot_total
    return total


def chips_bought_in(state: State) -> int:
    return sum(player["buy_in_total"] for player in state["players"].values())


def _eligible_seats(state: State) -> list[int]:
    """Seats that will be dealt into the next hand."""
    return sorted(
        number
        for number, seat in occupied(state).items()
        if seat.get("state") in (PLAYING, WAITING) and seat.get("stack", 0) > 0
    )


# --------------------------------------------------------------- identity


def join(nick: str, token: str, rejoin: str, host_code: str | None = None):
    """A new player sits down at the table for the first time."""
    nick = clean_text(nick, MAX_NICK_LENGTH, "Player")

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        if state.get("ended"):
            raise Rejected("This game has ended.")
        player_id = identity.new_player_id()
        first = not state["players"]
        player = identity.new_player(player_id, nick, token, rejoin, is_host=first)
        state["players"][player_id] = player
        if first:
            # The first person through the door is the host, and gets the
            # host code that lets a trusted friend take over later.
            state["host_code_hash"] = identity.hash_secret(host_code or token)
        note(state, "joined", player_id=player_id, nick=nick)
        return state, [
            Event("player_joined", {"player_id": player_id, "nick": nick}, player_id)
        ]

    return mutate


def regenerate_rejoin(player_id: str, rejoin: str):
    """Issue a new rejoin link, which invalidates the old one."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        player["rejoin_hash"] = identity.hash_secret(rejoin)
        note(state, "rejoin_regenerated", player_id=player_id)
        return state, [Event("rejoin_regenerated", {}, player_id)]

    return mutate


def adopt_token(player_id: str, token: str):
    """Attach a new device's token to an existing player.

    Used after a rejoin link or a host-approved claim: from then on that
    device reconnects on its own, and the previous device's token stops
    working — one active device per player.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        player["token_hash"] = identity.hash_secret(token)
        note(state, "device_changed", player_id=player_id)
        return state, [Event("device_changed", {}, player_id)]

    return mutate


def request_claim(claim_id: str, player_id: str, token: str, nick: str):
    """"I'm an existing player" on a new device, pending host approval."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        state["pending_claims"] = [
            c for c in state.get("pending_claims", []) if c["id"] != claim_id
        ] + [
            {
                "id": claim_id,
                "player_id": player_id,
                "token_hash": identity.hash_secret(token),
                "nick": clean_text(nick, MAX_NICK_LENGTH, "Player"),
                "at": identity.now_iso(),
            }
        ]
        note(state, "claim_requested", player_id=player_id)
        return state, [Event("claim_requested", {"claim": claim_id}, player_id)]

    return mutate


def resolve_claim(claim_id: str, approve: bool, by: str):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        if not identity.is_host(state, by):
            raise Rejected("Only the host can approve that.")
        claim = next(
            (c for c in state.get("pending_claims", []) if c["id"] == claim_id), None
        )
        if claim is None:
            raise Rejected("That request is no longer waiting.")
        state["pending_claims"] = [
            c for c in state["pending_claims"] if c["id"] != claim_id
        ]
        if approve:
            player = _require_player(state, claim["player_id"])
            player["token_hash"] = claim["token_hash"]
            note(state, "claim_approved", player_id=claim["player_id"], by=by)
        else:
            note(state, "claim_denied", player_id=claim["player_id"], by=by)
        return state, [
            Event(
                "claim_approved" if approve else "claim_denied",
                {"player_id": claim["player_id"]},
                by,
            )
        ]

    return mutate


def _require_player(state: State, player_id: str) -> dict:
    player = state["players"].get(player_id)
    if player is None:
        raise Rejected("That player is not at this table.")
    if player.get("revoked"):
        raise Rejected("Your access to this table was removed by the host.")
    return player


# ------------------------------------------------------------------ seats


def _check_seat_available(state: State, seat_number: int) -> None:
    """Can someone take this chair right now?

    A chair whose player folded and left is empty on screen but is still
    part of the hand being played — their chips are in the pot and the hand
    still has to pay out around them. Letting someone buy into it before the
    hand ends would put two players in one seat, so it waits.
    """
    if not 0 <= seat_number < state["settings"]["seat_count"]:
        raise Rejected("That seat does not exist.")

    row = state["seats"].get(str(seat_number))
    if row and row.get("player_id"):
        raise Rejected("Someone is already in that seat.")

    hand = _load_hand(state)
    if hand is not None and hand.street != holdem.COMPLETE and seat_number in hand.seats:
        raise Rejected("That seat is still in this hand. You can take it next hand.")


def _take_seat(
    state: State, player_id: str, seat_number: int, buy_in: int | None, resume: bool
) -> list[Event]:
    """Put a player in a seat, in place, and return what to log.

    Split out of `sit` so the waitlist can seat someone too, without going
    back round the settle loop it is already inside.
    """
    player = _require_player(state, player_id)
    settings = state["settings"]

    if state.get("ended"):
        raise Rejected("This game has ended.")
    if seat_of(state, player_id) is not None:
        raise Rejected("You are already sitting down.")
    _check_seat_available(state, seat_number)

    if resume:
        amount = player["held"]
        if amount <= 0:
            raise Rejected("You have no chips held.")
        # Resuming is not a buy-in: neither counter moves, and the
        # buy-in minimum and maximum do not apply to your own chips.
        ledger.resume_held(state, player, amount)
        events = [Event("resume_held", {"amount": amount}, player_id)]
        note(state, "resumed", player_id=player_id, amount=amount)
    else:
        amount = _buy_in_amount(settings, buy_in)
        _check_rathole(state, player, amount)
        ledger.buy_in(state, player, amount)
        events = [Event("buy_in", {"amount": amount}, player_id)]
        note(state, "bought_in", player_id=player_id, amount=amount)

    # A player who sits down mid-hand waits for the next one.
    in_hand = state.get("hand") is not None
    state["seats"][str(seat_number)] = {
        "player_id": player_id,
        "stack": amount,
        "state": WAITING if in_hand else PLAYING,
    }
    note(state, "sat_down", player_id=player_id, seat=seat_number)
    events.append(Event("sat_down", {"seat": seat_number}, player_id))
    return events


def _check_rathole(state: State, player: dict, amount: int) -> None:
    """You cannot buy back in for less than you left with, for a while.

    "Ratholing" is leaving with a big stack and sitting back down with a
    small one, which takes the money off the table. The host sets how long
    the restriction lasts; 0 switches it off.
    """
    minutes = int(state["settings"].get("rathole_minutes", 0))
    if minutes <= 0:
        return
    left_with = player.get("left_with")
    left_at = player.get("left_at")
    if not left_with or not left_at:
        return
    try:
        gone = (
            datetime.fromisoformat(identity.now_iso())
            - datetime.fromisoformat(left_at)
        ).total_seconds()
    except ValueError:
        return
    if gone >= minutes * 60:
        return
    if amount < left_with:
        raise Rejected(
            f"You left with {left_with}, so you cannot buy in for less than "
            f"that for another {int((minutes * 60 - gone) / 60) + 1} minutes."
        )


def sit(player_id: str, seat_number: int, buy_in: int | None, resume: bool):
    """Take a seat, either buying in or resuming chips you already own."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        events = _take_seat(state, player_id, seat_number, buy_in, resume)
        return _settle_table(state, events)

    return mutate


def _buy_in_amount(settings: dict, requested: int | None) -> int:
    if settings.get("fixed_buy_in"):
        return int(settings["fixed_buy_in"])
    amount = int(requested if requested is not None else settings["buy_in"])
    low, high = settings["min_buy_in"], settings["max_buy_in"]
    if amount < low or amount > high:
        raise Rejected(f"Buy in for between {low} and {high}.")
    return amount


def stand_up(player_id: str):
    """Leave the seat. The stack goes with you and cannot be lost.

    Standing up mid-hand takes effect when the hand ends, with whatever is
    left at that point.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        seat_number = seat_of(state, player_id)
        if seat_number is None:
            raise Rejected("You are not sitting down.")

        if _is_in_current_hand(state, player_id):
            state["seats"][str(seat_number)]["leaving"] = True
            note(state, "leaving_at_end_of_hand", player_id=player_id)
            return state, [Event("leaving", {"seat": seat_number}, player_id)]

        return _settle_table(state, _release_seat(state, seat_number, player_id))

    return mutate


def _release_seat(state: State, seat_number: int, player_id: str) -> list[Event]:
    """Empty a seat, holding whatever was in front of it on the record.

    Returns the events; it deliberately does not settle the table, because
    it is also called while a hand is being wound up.
    """
    hand = _load_hand(state)
    seat = state["seats"].pop(str(seat_number))
    player = state["players"][player_id]
    amount = seat.get("stack", 0)

    if (
        hand is not None
        and hand.street != holdem.COMPLETE
        and seat_number in hand.seats
        and hand.seats[seat_number].player_id == player_id
    ):
        # Leaving while the hand is still running — which only a player who
        # has folded can do. Chips already pushed into the middle belong to
        # the pot now, so what they take with them is what is in front of
        # them, not what they sat down with.
        amount = hand.seats[seat_number].stack

    if amount:
        ledger.hold(state, player, amount)
    # What they left with and when, for the rathole rule (section 7).
    player["left_with"] = amount
    player["left_at"] = identity.now_iso()
    note(state, "stood_up", player_id=player_id, seat=seat_number, held=amount)
    return [Event("stood_up", {"seat": seat_number, "held": amount}, player_id)]


def set_away(player_id: str, away: bool):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        seat_number = seat_of(state, player_id)
        if seat_number is None:
            raise Rejected("You are not sitting down.")
        seat = state["seats"][str(seat_number)]
        seat["state"] = AWAY if away else PLAYING
        note(state, "away" if away else "back", player_id=player_id)
        return _settle_table(
            state, [Event("away" if away else "back", {}, player_id)]
        )

    return mutate


def cash_out(player_id: str):
    """Take chips off the table for good, recorded in the ledger."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        if _is_in_current_hand(state, player_id):
            raise Rejected("You can cash out once this hand is over.")

        seat_number = seat_of(state, player_id)
        amount = player["held"]
        if seat_number is not None:
            amount += state["seats"][str(seat_number)].get("stack", 0)
            state["seats"].pop(str(seat_number))
        if amount <= 0:
            raise Rejected("You have no chips to cash out.")

        player["held"] = 0
        ledger.cash_out(state, player, amount)
        note(state, "cashed_out", player_id=player_id, amount=amount)
        return _settle_table(
            state, [Event("cash_out", {"amount": amount}, player_id)]
        )

    return mutate


def resume_cash_out(player_id: str, seat_number: int):
    """Come back for exactly what you cashed out. Not a new buy-in."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        setting = state["settings"].get("resume_after_cash_out", "allowed")
        if setting == "off":
            raise Rejected("The host has turned off coming back after a cash-out.")
        if state.get("ended"):
            raise Rejected("This game has ended.")
        if seat_of(state, player_id) is not None:
            raise Rejected("You are already sitting down.")
        _check_seat_available(state, seat_number)

        previous = ledger.open_cash_outs(state, player_id)
        if not previous:
            raise Rejected("You have no cash-out to come back with.")
        original = previous[0]

        ledger.reverse_cash_out(state, player, original)
        in_hand = state.get("hand") is not None
        state["seats"][str(seat_number)] = {
            "player_id": player_id,
            "stack": original["amount"],
            "state": WAITING if in_hand else PLAYING,
        }
        note(
            state,
            "resumed_cash_out",
            player_id=player_id,
            amount=original["amount"],
        )
        return _settle_table(
            state,
            [Event("cash_out_reversed", {"amount": original["amount"]}, player_id)],
        )

    return mutate


# ------------------------------------------------------------ playing


def act(player_id: str, action: str, amount: int | None):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        hand = _load_hand(state)
        if hand is None:
            raise Rejected("There is no hand in progress.")

        seat_number = seat_of(state, player_id)
        if seat_number is None or seat_number not in hand.seats:
            raise Rejected("You are not in this hand.")
        if hand.seats[seat_number].player_id != player_id:
            raise Rejected("You are not in this hand.")

        try:
            hand.act(seat_number, action, amount)
        except holdem.IllegalAction as exc:
            raise Rejected(str(exc)) from None

        events = [
            Event(
                "action",
                {"action": action, "amount": amount, "seat": seat_number},
                player_id,
            )
        ]
        note(state, "action", player_id=player_id, action=action, amount=amount)
        state["hand"] = hand.to_dict()
        return _settle_table(state, events)

    return mutate


def join_waitlist(player_id: str, seat: int | None = None):
    """Ask for a seat, or for a particular seat, when none is free.

    One queue serves both: with no seat named it is "next free chair",
    with one it is a seat-change request for that chair. Either way the
    player is seated automatically as soon as it can be done between
    hands, so nobody has to watch for an opening.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        wanted = int(seat) if seat is not None else None
        if wanted is not None and not 0 <= wanted < state["settings"]["seat_count"]:
            raise Rejected("There is no such seat at this table.")

        queue = [w for w in state.get("waitlist", []) if w["player_id"] != player_id]
        queue.append(
            {"player_id": player_id, "seat": wanted, "at": identity.now_iso()}
        )
        state["waitlist"] = queue
        note(state, "waitlist_joined", player_id=player_id, seat=wanted)
        return _settle_table(
            state,
            [Event("waitlist_joined", {"seat": wanted}, player_id)],
        )

    return mutate


def leave_waitlist(player_id: str):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        before = len(state.get("waitlist", []))
        state["waitlist"] = [
            w for w in state.get("waitlist", []) if w["player_id"] != player_id
        ]
        if len(state["waitlist"]) == before:
            raise Rejected("You are not on the waitlist.")
        note(state, "waitlist_left", player_id=player_id)
        return state, [Event("waitlist_left", {}, player_id)]

    return mutate


def set_note(player_id: str, about: str, text: str):
    """A private note about another player.

    Kept on the server so it follows the player to a new device, and sent
    only to its author — `view_for` never puts one in anybody else's
    snapshot.
    """
    text = clean_text(text, MAX_NOTE_LENGTH)

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        if about not in state["players"]:
            raise Rejected("There is nobody here by that name.")
        if about == player_id:
            raise Rejected("You cannot keep a note about yourself.")

        notes = dict(player.get("notes", {}))
        if text:
            notes[about] = text
        else:
            notes.pop(about, None)
        player["notes"] = notes
        # Deliberately not written to the table log: a private note that
        # shows up in a log everyone can read is not private.
        return state, [Event("note_saved", {"about": about}, player_id)]

    return mutate


def choose_runs(player_id: str, count: int, now: float | None = None):
    """Pick how many times to run the board (section 6.6).

    Everyone still in the hand gets a say. The lowest number anybody asks
    for is the one used, so running it more only ever happens by agreement,
    and the runs start as soon as everyone has confirmed — or when the
    timer ends, whichever comes first.
    """
    at = time.time() if now is None else now

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        hand = _load_hand(state)
        if hand is None or hand.street != holdem.RUN_CHOICE:
            raise Rejected("There is nothing to run more than once right now.")

        seat_number = seat_of(state, player_id)
        if seat_number is None or seat_number not in hand.run_voters():
            raise Rejected("You are not in this hand.")

        try:
            hand.choose_runs(seat_number, int(count))
        except holdem.IllegalAction as exc:
            raise Rejected(str(exc)) from None

        state["hand"] = hand.to_dict()
        note(state, "run_choice", player_id=player_id, count=int(count))
        return _settle_table(
            state,
            [Event("run_choice", {"seat": seat_number, "count": int(count)}, player_id)],
            at,
        )

    return mutate


def show_or_muck(player_id: str, cards: list[int] | None, now: float | None = None):
    """Turn your hand over at showdown, or let it go (section 6.8).

    `cards` is which of your two to show: both, one of them, or none at
    all. Showing a hand that cannot win is always optional.
    """
    at = time.time() if now is None else now

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        pending = state.get("showdown")
        if not pending or not pending.get("waiting"):
            raise Rejected("There is nothing to show right now.")

        seat_number = seat_of(state, player_id)
        if seat_number is None or seat_number not in pending["waiting"]:
            raise Rejected("That is not your decision to make.")

        events = _settle_showdown_seat(state, seat_number, cards or [])
        return _settle_table(state, events, at)

    return mutate


def set_auto_muck(player_id: str, on: bool):
    """A per-player preference: never be asked, just let losers go."""

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        player["auto_muck"] = bool(on)
        return state, [Event("auto_muck", {"on": bool(on)}, player_id)]

    return mutate


def reveal(player_id: str, index: int):
    """Rabbit hunting: flip one of the cards the hand never got to (6.7).

    Only between hands, only when the host has it switched on, and the card
    is shown to the whole table once anyone turns it over. There is no vote
    and no cost, and the log records who revealed what.
    """

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        if not state["settings"].get("rabbit_hunting", False):
            raise Rejected("Rabbit hunting is switched off at this table.")
        if state.get("hand"):
            raise Rejected("Wait until the hand is over.")

        last = state.get("last_hand")
        if not last or not last.get("rabbit"):
            raise Rejected("There is nothing left to see.")

        rabbit = last["rabbit"]
        position = int(index)
        if not 0 <= position < len(rabbit["cards"]):
            raise Rejected("That card is not there.")
        if position in rabbit["revealed"]:
            raise Rejected("That one is already face up.")

        rabbit["revealed"] = sorted({*rabbit["revealed"], position})
        card = rabbit["cards"][position]
        note(state, "rabbit", player_id=player_id, nick=player["nick"], card=card)
        return state, [
            Event("rabbit", {"index": position, "card": card}, player_id)
        ]

    return mutate


def react(player_id: str, emoji: str, now: float | None = None):
    """An emoji beside your seat for a few seconds."""
    at = time.time() if now is None else now
    # One glyph, and only from the set the client offers.
    emoji = clean_text(emoji, 8)

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        _require_player(state, player_id)
        if emoji not in REACTIONS:
            raise Rejected("That is not one of the reactions.")
        seat_number = seat_of(state, player_id)
        fresh = [
            r for r in state.get("reactions", []) if r["until"] > at
        ]
        state["reactions"] = (
            fresh + [{"player_id": player_id, "seat": seat_number,
                      "emoji": emoji, "until": at + REACTION_SECONDS}]
        )[-MAX_REACTIONS:]
        return state, [Event("reaction", {"emoji": emoji}, player_id)]

    return mutate


def say(player_id: str, text: str):
    text = clean_text(text, MAX_CHAT_LENGTH)

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        player = _require_player(state, player_id)
        if not text:
            raise Rejected("That message was empty.")
        state["messages"] = (
            state.get("messages", [])
            + [
                {
                    "id": identity.new_player_id(),
                    "player_id": player_id,
                    "nick": player["nick"],
                    "text": text,
                    "at": identity.now_iso(),
                }
            ]
        )[-MAX_MESSAGES:]
        return state, [Event("chat", {"text": text}, player_id)]

    return mutate


def pause(by: str, paused: bool):
    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        if not identity.is_host(state, by):
            raise Rejected("Only the host can do that.")
        state["paused"] = paused
        note(state, "paused" if paused else "resumed", by=by)
        return _settle_table(
            state, [Event("paused" if paused else "resumed", {}, by)]
        )

    return mutate


# ------------------------------------------------------- the hand lifecycle


def _load_hand(state: State) -> holdem.Hand | None:
    data = state.get("hand")
    return holdem.Hand.from_dict(data) if data else None


def _is_in_current_hand(state: State, player_id: str) -> bool:
    hand = _load_hand(state)
    if hand is None or hand.street == holdem.COMPLETE:
        return False
    seat_number = seat_of(state, player_id)
    if seat_number is None or seat_number not in hand.seats:
        return False
    return not hand.seats[seat_number].folded


def _settle_table(
    state: State, events: list[Event], now: float | None = None
) -> tuple[State, list[Event]]:
    """Move the table as far forward as it can go on its own.

    Called after every action that could change what happens next. It runs
    as a loop because one action can cascade: the last caller folds, the
    hand pays out, the next hand deals, and the player it deals to is away
    and folds immediately. Each pass does one step; the bound stops a bug
    from turning into a spin.
    """
    events = list(events)
    now = time.time() if now is None else now

    for _ in range(64):
        hand = _load_hand(state)

        if hand is not None and hand.street == holdem.RUN_CHOICE:
            if _run_choice_settled(state, hand, now):
                hand.resolve_runs()
                state["hand"] = hand.to_dict()
                note(state, "runs_settled", hand_no=hand.hand_no)
                events.append(Event("runs_settled", {"hand_no": hand.hand_no}))
                continue
            break

        if hand is not None and hand.street == holdem.COMPLETE:
            # Anyone who may show or muck gets their moment before the
            # chips are swept and the next hand is dealt.
            if _open_showdown_choice(state, hand, now):
                break
            events += _finish_hand(state, hand)
            state["hand"] = None
            gap = int(state["settings"].get("between_hands_seconds", 0))
            state["between_hands_until"] = now + gap if gap > 0 else None
            continue

        if hand is None and _can_start(state, now):
            events += _begin_hand(state)
            continue

        # Between hands, take the next person off the waitlist.
        seated = _seat_from_waitlist(state)
        if seated is not None:
            events.append(seated)
            continue

        # An away player does not keep the table waiting for a clock.
        folded = _auto_fold_away(state, now)
        if folded is not None:
            events.append(folded)
            continue

        break

    _refresh_deadline(state, now)
    _refresh_equity(state, _load_hand(state))
    return state, events


def _open_showdown_choice(state: State, hand: holdem.Hand, now: float) -> bool:
    """Pause on a finished hand while players decide to show or muck.

    Returns True while somebody still has a decision to make. A player who
    has asked to muck automatically never holds the table up.
    """
    if state.get("showdown") is not None:
        return bool(state["showdown"].get("waiting"))

    # Nobody had to beat anybody: the last player standing may show if they
    # want to, but the table does not wait on a flourish.
    if (hand.result or {}).get("uncontested"):
        return False

    # The host can switch the decision off. Nought seconds has to mean
    # "do not ask", never "ask with no way to finish" — that would stop
    # the table dead waiting for an answer nothing could end.
    seconds = int(state["settings"].get("showdown_seconds", 0))
    if seconds <= 0:
        return False

    choosers = [s for s in hand.show_choice if s in hand.seats]
    waiting = []
    for seat_number in choosers:
        player_id = hand.seats[seat_number].player_id
        player = state["players"].get(player_id) or {}
        if player.get("auto_muck", False):
            continue
        waiting.append(seat_number)

    if not waiting:
        return False

    state["showdown"] = {"hand_no": hand.hand_no, "waiting": waiting, "shown": {}}
    clock.set_on(
        state, clock.make(clock.SHOWDOWN, now + seconds, hand_no=hand.hand_no)
    )
    note(state, "showdown_choice", seats=waiting)
    return True


def _settle_showdown_seat(
    state: State, seat_number: int, show: list[int] | None
) -> list[Event]:
    """Record one player's show-or-muck, and take them off the waiting list.

    `show` is which of their two cards to turn over: both, one, or none.
    `None` means they ran out of time, which shows a hand that has to be
    shown and mucks one that does not.
    """
    pending = state.get("showdown")
    hand = _load_hand(state)
    if pending is None or hand is None:
        return []
    if seat_number not in pending.get("waiting", []):
        return []

    pending["waiting"] = [s for s in pending["waiting"] if s != seat_number]
    seat = hand.seats.get(seat_number)
    if seat is None:
        return []

    cards = list(seat.hole)
    if show is None:
        chosen: list[int] = []  # out of time: a hand nobody has to see is mucked
    else:
        chosen = sorted({i for i in show if 0 <= i < len(cards)})

    player_id = seat.player_id
    if chosen:
        pending["shown"][str(seat_number)] = [cards[i] for i in chosen]
        note(state, "showed", seat=seat_number,
             cards=[cards[i] for i in chosen])
        return [
            Event(
                "showed",
                {"seat": seat_number, "cards": [cards[i] for i in chosen]},
                player_id,
            )
        ]

    note(state, "mucked", seat=seat_number)
    return [Event("mucked", {"seat": seat_number}, player_id)]


def _run_choice_settled(state: State, hand: holdem.Hand, now: float) -> bool:
    """Run-it-more starts when everyone has confirmed or the timer ends."""
    if hand.everyone_has_chosen():
        return True
    deadline = clock.get(state)
    if deadline and deadline["kind"] == clock.RUN_CHOICE:
        return clock.is_due(deadline, now)
    # No timer is running yet; the deadline is set on the way out.
    return False


def _seat_from_waitlist(state: State) -> Event | None:
    """Seat one waiting player, if a chair they will take is free.

    Only between hands, and only one per pass, so the settle loop writes
    one event per seat taken. Someone waiting on a particular chair keeps
    waiting until that chair is free; someone waiting on any chair takes
    the lowest free one.
    """
    if state.get("hand") or state.get("ended"):
        return None
    queue = state.get("waitlist") or []
    if not queue:
        return None

    taken = {int(n) for n in state["seats"]}
    free = [n for n in range(state["settings"]["seat_count"]) if n not in taken]
    if not free:
        return None

    for index, waiting in enumerate(queue):
        player_id = waiting["player_id"]
        player = state["players"].get(player_id)
        if player is None:
            state["waitlist"] = [w for w in queue if w["player_id"] != player_id]
            return Event("waitlist_dropped", {}, player_id)
        if seat_of(state, player_id) is not None:
            # They got a seat another way; take them off the queue.
            state["waitlist"] = [w for w in queue if w["player_id"] != player_id]
            return Event("waitlist_left", {}, player_id)

        wanted = waiting.get("seat")
        if wanted is not None and wanted not in free:
            continue
        number = wanted if wanted is not None else free[0]

        # Resuming held chips is not a buy-in, so it is preferred when
        # they have some (section 6.10).
        resume = bool(player.get("held"))
        try:
            _take_seat(state, player_id, number, None, resume)
        except Rejected:
            # They cannot sit right now (no chips, say). Leave them queued
            # and let somebody behind them through.
            continue

        state["waitlist"] = [
            w for w in state.get("waitlist", []) if w["player_id"] != player_id
        ]
        note(state, "waitlist_seated", player_id=player_id, seat=number)
        return Event("waitlist_seated", {"seat": number}, player_id)

    return None


def _auto_fold_away(state: State, now: float) -> Event | None:
    """Fold for a player who is sitting out when the action reaches them."""
    hand = _load_hand(state)
    if hand is None or hand.to_act is None:
        return None
    seat_number = hand.to_act
    row = state["seats"].get(str(seat_number))
    if row is None or row.get("state") != AWAY:
        return None

    action = "check" if hand.legal_actions(seat_number)["to_call"] == 0 else "fold"
    hand.act(seat_number, action, None)
    state["hand"] = hand.to_dict()
    note(state, "auto_action", seat=seat_number, action=action, reason="away")
    return Event(
        "auto_action",
        {"seat": seat_number, "action": action, "reason": "away"},
        row.get("player_id"),
    )


def _refresh_deadline(state: State, now: float) -> None:
    """Keep exactly one countdown running, for whatever is being waited on.

    The deadline is replaced only when what is being waited on changes, so a
    player's clock is not quietly restarted by an unrelated event — someone
    else's chat message must not buy them another thirty seconds.
    """
    settings = state["settings"]
    hand = _load_hand(state)
    current = clock.get(state)

    wanted: dict[str, Any] | None = None

    if state.get("paused") or state.get("ended"):
        wanted = None

    elif (state.get("showdown") or {}).get("waiting"):
        seconds = int(settings.get("showdown_seconds", 0))
        if seconds > 0:
            wanted = (
                current
                if current and current["kind"] == clock.SHOWDOWN
                else clock.make(
                    clock.SHOWDOWN,
                    now + seconds,
                    hand_no=state["showdown"]["hand_no"],
                )
            )

    elif (
        hand is None
        and state.get("between_hands_until") is not None
        and now < state["between_hands_until"]
        and len(_eligible_seats(state)) >= 2
    ):
        wanted = clock.make(clock.NEXT_HAND, state["between_hands_until"])

    elif hand is not None and hand.street == holdem.RUN_CHOICE:
        seconds = int(settings.get("run_timer_seconds", 10))
        wanted = clock.make(
            clock.RUN_CHOICE, now + seconds, hand_no=hand.hand_no, seat=None
        )
        if current and current["kind"] == clock.RUN_CHOICE:
            wanted = current  # already counting down; leave it alone

    elif hand is not None and hand.to_act is not None:
        seconds = int(settings.get("action_seconds", 0))
        if seconds > 0:
            same = (
                current
                and current["kind"] == clock.ACTION
                and current.get("seat") == hand.to_act
                and current.get("hand_no") == hand.hand_no
            )
            wanted = (
                current
                if same
                else clock.make(
                    clock.ACTION,
                    now + seconds,
                    hand_no=hand.hand_no,
                    seat=hand.to_act,
                    phase=clock.PHASE_CLOCK,
                )
            )

    clock.set_on(state, wanted)


def _time_bank_left(state: State, seat_number: int) -> int:
    row = state["seats"].get(str(seat_number)) or {}
    default = int(state["settings"].get("time_bank_seconds", 0))
    return int(row.get("time_bank", default))


def _spend_time_bank(state: State, seat_number: int, seconds: float) -> None:
    row = state["seats"].get(str(seat_number))
    if row is None:
        return
    left = _time_bank_left(state, seat_number)
    row["time_bank"] = max(0, int(round(left - seconds)))


def tick(now: float | None = None):
    """Apply whatever the passing of time has made due.

    This is an action like any other: it carries an id, it is saved in the
    same transaction as its effects, and replaying it changes nothing. The
    server submits one when a deadline falls due (see main.py).
    """
    at = time.time() if now is None else now

    def mutate(state: State) -> tuple[State, list[Event]]:
        state = _clone(state)
        deadline = clock.get(state)
        if not clock.is_due(deadline, at):
            # Nothing was due after all — a stale wake-up, or someone acted
            # first. Settling is still safe and keeps the clock honest.
            return _settle_table(state, [], at)

        events: list[Event] = []
        kind = deadline["kind"]

        if kind == clock.RUN_CHOICE:
            # Time is up: deal them. Anyone who did not choose counts as
            # having asked for one, and the lowest number asked for wins.
            hand = _load_hand(state)
            if hand is not None and hand.street == holdem.RUN_CHOICE:
                hand.resolve_runs()
                state["hand"] = hand.to_dict()
                note(state, "run_choice_timeout", hand_no=hand.hand_no)
                events.append(
                    Event("runs_settled", {"hand_no": hand.hand_no})
                )
            clock.clear(state)

        elif kind == clock.ACTION:
            events += _expire_action_clock(state, deadline, at)

        elif kind == clock.SHOWDOWN:
            events += _expire_showdown(state, deadline)

        elif kind == clock.NEXT_HAND:
            # The gap is over; _settle_table deals the next hand below.
            state["between_hands_until"] = None
            clock.clear(state)

        return _settle_table(state, events, at)

    return mutate


def _expire_action_clock(
    state: State, deadline: dict[str, Any], now: float
) -> list[Event]:
    """A player ran out of time: use the time bank, or act for them."""
    hand = _load_hand(state)
    seat_number = deadline.get("seat")
    if hand is None or hand.to_act is None or hand.to_act != seat_number:
        clock.clear(state)
        return []

    row = state["seats"].get(str(seat_number)) or {}
    player_id = row.get("player_id")

    if deadline.get("phase") == clock.PHASE_CLOCK:
        bank = _time_bank_left(state, seat_number)
        if bank > 0:
            clock.set_on(
                state,
                clock.make(
                    clock.ACTION,
                    now + bank,
                    hand_no=hand.hand_no,
                    seat=seat_number,
                    phase=clock.PHASE_BANK,
                    bank_started=now,
                ),
            )
            note(state, "time_bank_started", seat=seat_number, seconds=bank)
            return [
                Event("time_bank", {"seat": seat_number, "seconds": bank}, player_id)
            ]

    # Out of time and out of bank. Check if it is free, otherwise fold.
    _spend_time_bank(state, seat_number, _time_bank_left(state, seat_number))
    action = "check" if hand.legal_actions(seat_number)["to_call"] == 0 else "fold"
    hand.act(seat_number, action, None)
    state["hand"] = hand.to_dict()
    clock.clear(state)
    note(state, "auto_action", seat=seat_number, action=action, reason="timeout")
    return [
        Event(
            "auto_action",
            {"seat": seat_number, "action": action, "reason": "timeout"},
            player_id,
        )
    ]


def _expire_showdown(state: State, deadline: dict[str, Any]) -> list[Event]:
    """Nobody decided in time: show every hand that has to be shown.

    A hand that wins or ties has to be shown to take the pot, so the server
    turns it over rather than letting a pot go unclaimed (section 6.8).
    """
    clock.clear(state)
    pending = state.get("showdown") or {}
    if not pending:
        return []
    events: list[Event] = []
    for seat_number in list(pending.get("waiting", [])):
        events += _settle_showdown_seat(state, int(seat_number), show=None)
    return events


def _can_start(state: State, now: float | None = None) -> bool:
    if state.get("hand") or state.get("paused") or state.get("ended"):
        return False
    if (state.get("showdown") or {}).get("waiting"):
        return False
    # The pause between hands: time to see the winner and to rabbit hunt.
    until = state.get("between_hands_until")
    if until is not None and (time.time() if now is None else now) < until:
        return False
    return len(_eligible_seats(state)) >= 2


def _finish_hand(state: State, hand: holdem.Hand) -> list[Event]:
    """Put the chips back on the seats and write the hand to history."""
    events: list[Event] = []
    result = hand.result or {}

    for seat_number, seat in hand.seats.items():
        row = state["seats"].get(str(seat_number))
        if row and row.get("player_id") == seat.player_id:
            row["stack"] = seat.stack

    # The trophy badge beside a name: hands won this session.
    if result.get("uncontested"):
        winners = {s for s in hand.seats if not hand.seats[s].folded}
    else:
        winners = {
            seat
            for run in result.get("runs", [])
            for pot in run.get("pots", [])
            for seat in pot.get("winners", [])
        }
    for seat_number in winners:
        player = state["players"].get(hand.seats[seat_number].player_id)
        if player:
            player["hands_won"] = player.get("hands_won", 0) + 1

    # When the hand ended all in, the board is dealt in one go but the
    # client plays it out card by card. These are the percentages at the
    # moment the hands went face up, computed here from the cards that were
    # public then, so the run-out can be watched with real numbers against
    # it rather than numbers made up in the browser.
    standing = _equity_at_all_in(hand)
    if standing:
        result = {**result, "equity_at_all_in": standing}

    # Cards a player chose to turn over, on top of the ones they had to.
    pending = state.get("showdown") or {}
    if pending.get("shown"):
        result = {**result, "volunteered": dict(pending["shown"])}
    state["showdown"] = None

    state["last_hand"] = {
        "hand_no": hand.hand_no,
        "boards": hand.boards(),
        "result": result,
        "button": hand.button,
        "rabbit": _rabbit_cards(state, hand),
    }

    note(state, "hand_complete", hand_no=hand.hand_no, awards=result.get("awards"))
    events.append(
        Event("hand_complete", {"hand_no": hand.hand_no, "result": result})
    )
    # The whole hand, for the append-only history table. This carries hole
    # cards and the rest of the deck, so it goes to the server to file away
    # and is never part of a state snapshot or sent to a client.
    events.append(
        Event("hand_history", {"hand_no": hand.hand_no, "hand": hand.to_dict()})
    )

    # Anyone who asked to leave during the hand goes now, and anyone who
    # busted stays in their seat with nothing in front of them.
    for seat_number, row in list(state["seats"].items()):
        if row.get("leaving"):
            player_id = row["player_id"]
            row.pop("leaving", None)
            events += _release_seat(state, int(seat_number), player_id)
        elif row.get("state") == WAITING:
            row["state"] = PLAYING

    return events


def _equity_at_all_in(hand: holdem.Hand) -> dict[str, Any] | None:
    """Win and tie percentages as they stood when the hands went face up.

    Only for a hand that ended with the board still to come, which is the
    only time anybody wants them. One calculation per hand, from the shared
    board and the hands that were turned over — never from a mucked or
    folded hand.
    """
    if not hand.run_boards or len(hand.board) >= 5:
        return None
    face_up = {
        seat_no: list(seat.hole)
        for seat_no, seat in hand.seats.items()
        if seat.revealed and not seat.folded and seat.hole
    }
    if len(face_up) < 2:
        return None
    try:
        odds = equity.equities(face_up, list(hand.board))
    except Exception:  # noqa: BLE001 - never let a display number stop a hand
        return None
    return {
        "board": list(hand.board),
        "seats": {str(seat): value for seat, value in odds.items()},
    }


def _rabbit_cards(state: State, hand: holdem.Hand) -> dict[str, Any] | None:
    """The board the hand never reached, kept face down for rabbit hunting.

    The cards are held here but never sent to anyone: `view_for` passes on
    only the ones somebody has actually turned over, and only the count of
    the rest, so a face-down card is face down in the network traffic too.
    """
    if not state["settings"].get("rabbit_hunting", False):
        return None
    if len(hand.board) >= 5:
        return None

    # Deal them the way the hand itself would have: a burn before each
    # street, so the cards shown are the cards that would have come.
    remaining = {0: (holdem.FLOP, holdem.TURN, holdem.RIVER),
                 3: (holdem.TURN, holdem.RIVER),
                 4: (holdem.RIVER,)}.get(len(hand.board))
    if remaining is None:
        return None

    deck = list(hand.deck)
    cards: list[str] = []
    for street in remaining:
        count = holdem.BOARD_PLAN[street]
        if len(deck) < 1 + count:
            return None
        deck.pop(0)  # burn
        cards.extend(deck.pop(0) for _ in range(count))

    return {"cards": cards, "revealed": [], "from": len(hand.board)}


def _begin_hand(state: State) -> list[Event]:
    seats = _eligible_seats(state)
    button, sb_seat, bb_seat = next_blinds(state, seats)

    config = holdem.Config(
        small_blind=state["settings"]["small_blind"],
        big_blind=state["settings"]["big_blind"],
        ante=state["settings"]["ante"],
        ante_type=state["settings"]["ante_type"],
        structure=state["settings"]["structure"],
        preflop_limit=state["settings"]["preflop_limit"],
        straddle=state.get("next_straddle"),
        # One run unless the players ask for more: the engine pauses and
        # asks only when somebody is all in with board still to come.
        runs=1,
    )
    max_runs = max(1, int(state["settings"].get("max_runs", 1)))

    state["hand_no"] = state.get("hand_no", 0) + 1
    hand = holdem.start_hand(
        hand_no=state["hand_no"],
        button=button,
        stacks={
            seat: (state["seats"][str(seat)]["player_id"], state["seats"][str(seat)]["stack"])
            for seat in seats
        },
        config=config,
        sb_seat=sb_seat,
        bb_seat=bb_seat,
        max_runs=max_runs,
    )

    # A seat's `stack` is what that player owns at the table, which does not
    # change just because chips have moved into the middle. While a hand
    # runs, the live "in front of them" number comes from the hand; the seat
    # is written back once, when the hand is paid out. That way the ledger
    # adds up at every moment, including mid-hand.
    state["hand"] = hand.to_dict()
    state["button"] = button
    state["last_bb_seat"] = bb_seat
    state["last_sb_seat"] = sb_seat
    state["next_straddle"] = None
    note(state, "hand_started", hand_no=hand.hand_no, button=button)

    events = [Event("hand_started", {"hand_no": hand.hand_no, "button": button})]
    if hand.street == holdem.COMPLETE:
        # Everyone was all in for their blinds; it is already over.
        events += _finish_hand(state, hand)
        state["hand"] = None
    return events


def next_blinds(state: State, seats: list[int]) -> tuple[int, int | None, int]:
    """Where the button and the blinds go this hand.

    This is the standard dead-button rule, and everything in it follows from
    one invariant: **the big blind never moves backwards.** Each hand it
    advances to the next seat that is playing, and the rest is derived:

      * the big blind is the next eligible seat after last hand's;
      * the small blind goes where the big blind was. If that player has
        gone, nobody posts it — a dead small blind;
      * the button goes where the small blind was, even if that chair is now
        empty — a dead button.

    So a player who busts or leaves never causes anyone to pay a blind twice
    or skip one. Heads-up is its own rule: the button is the small blind,
    and the two alternate.
    """
    seat_count = max(int(state["settings"].get("seat_count", 10)), 1)
    last_bb = state.get("last_bb_seat")
    last_sb = state.get("last_sb_seat")

    if len(seats) == 2:
        bb_seat = (
            next(s for s in seats if s != last_bb) if last_bb in seats else seats[1]
        )
        sb_seat = next(s for s in seats if s != bb_seat)
        # Heads-up the button and the small blind are the same seat.
        return sb_seat, sb_seat, bb_seat

    if last_bb is None:
        # First hand at this table: no history to respect.
        button = seats[0]
        after = _clockwise_from(seats, button)
        return button, after[0], after[1]

    bb_seat = _next_after(seats, last_bb)
    sb_seat = last_bb if last_bb in seats else None

    if last_sb is not None:
        button = last_sb
    elif sb_seat is not None:
        button = (sb_seat - 1) % seat_count
    else:
        button = (bb_seat - 2) % seat_count

    # The button is only a marker, but it must not land on a seat that is
    # posting a blind this hand, or the order of play would be wrong.
    if button == bb_seat or (sb_seat is not None and button == sb_seat):
        button = (min(filter(None.__ne__, (sb_seat, bb_seat))) - 1) % seat_count

    return button, sb_seat, bb_seat


def _clockwise_from(seats: list[int], start_after: int) -> list[int]:
    later = [s for s in seats if s > start_after]
    earlier = [s for s in seats if s <= start_after]
    return later + earlier


def _next_after(seats: list[int], start_after: int) -> int:
    return _clockwise_from(seats, start_after)[0]


# ------------------------------------------------------------------- views


def _run_choice_view(hand: holdem.Hand | None) -> dict[str, Any] | None:
    """What to show while the table decides how many times to run it."""
    if hand is None or hand.street != holdem.RUN_CHOICE:
        return None
    return {
        "max_runs": hand.max_runs,
        "voters": hand.run_voters(),
        "chosen": {str(seat): count for seat, count in hand.run_choices.items()},
    }


def _equity_fingerprint(hand: holdem.Hand | None) -> list | None:
    """What the percentages depend on: the board and who is face up."""
    if hand is None or hand.street == holdem.COMPLETE:
        return None
    face_up = sorted(
        seat_no
        for seat_no, seat in hand.seats.items()
        if seat.revealed and not seat.folded and seat.hole
    )
    if len(face_up) < 2 or len(hand.board) >= 5:
        return None
    return [hand.hand_no, list(hand.board), face_up]


def _refresh_equity(state: State, hand: holdem.Hand | None) -> None:
    """Recompute win and tie percentages, but only when they can have moved.

    Section 6.6 wants these updated as cards are dealt. They are worked out
    once per change rather than once per viewer: the enumeration is not
    cheap, and every viewer should be looking at the same number anyway.

    Only cards already face up to the whole table go in, so a percentage can
    never be back-solved into somebody's hole cards.
    """
    fingerprint = _equity_fingerprint(hand)
    if fingerprint is None:
        state["equity"] = None
        return
    current = state.get("equity")
    if current and current.get("of") == fingerprint:
        return

    face_up = {
        seat_no: list(seat.hole)
        for seat_no, seat in hand.seats.items()
        if seat.revealed and not seat.folded and seat.hole
    }
    try:
        odds = equity.equities(face_up, list(hand.board))
    except Exception:  # noqa: BLE001 - a display nicety must never stop a hand
        state["equity"] = None
        return
    state["equity"] = {
        "of": fingerprint,
        "seats": {str(seat): value for seat, value in odds.items()},
    }


def _equity_view(state: State) -> dict[str, Any] | None:
    stored = state.get("equity")
    return stored["seats"] if stored else None


def replay_for(
    stored: dict[str, Any], state: State, player_id: str | None
) -> dict[str, Any]:
    """One past hand, redacted for this viewer, as a list of steps.

    A stored hand holds everything: every hole card, the cards that were
    mucked, and the rest of the deck. Almost none of that may be handed
    back. What a viewer may see afterwards is what they could see at the
    time, which is:

      * their own cards, if they were in the hand;
      * any hand that was turned face up at showdown;
      * the board, the betting, and who won what.

    A hand that was mucked stays mucked — otherwise anyone could read back
    what their opponents were folding all night, which is a bigger edge
    than anything else on this site. The deck never leaves at all.
    """
    hand = holdem.Hand.from_dict(stored)
    result = hand.result or {}
    shown = result.get("shown", {}) or {}
    volunteered = result.get("volunteered", {}) or {}

    my_seat = None
    if player_id:
        for seat_no, seat in hand.seats.items():
            if seat.player_id == player_id:
                my_seat = seat_no
                break

    nicks = {}
    for seat_no, seat in hand.seats.items():
        player = state.get("players", {}).get(seat.player_id) or {}
        nicks[seat_no] = player.get("nick", f"Seat {seat_no}")

    seats = []
    for seat_no, seat in sorted(hand.seats.items()):
        public_cards: list[str] | None = None
        if str(seat_no) in shown:
            public_cards = list(shown[str(seat_no)]["hole"])
        elif str(seat_no) in volunteered:
            public_cards = list(volunteered[str(seat_no)])
        elif seat_no == my_seat:
            public_cards = list(seat.hole)

        seats.append(
            {
                "seat": seat_no,
                "nick": nicks[seat_no],
                "you": seat_no == my_seat,
                "starting_stack": hand.starting_stacks.get(seat_no, 0),
                "cards": public_cards,
                "mucked": public_cards is None and not seat.folded,
                "folded": seat.folded,
                "won": int(result.get("awards", {}).get(str(seat_no), 0)),
            }
        )

    return {
        "hand_no": hand.hand_no,
        "button": hand.button,
        "blinds": {
            "small": hand.config.small_blind,
            "big": hand.config.big_blind,
            "ante": hand.config.ante,
        },
        "seats": seats,
        "boards": hand.boards(),
        # The engine's own log is already a step-by-step account of the
        # hand, so the replay is that list with names attached.
        "steps": [
            {**entry, "nick": nicks.get(entry.get("seat"))}
            for entry in hand.log
        ],
        "result": {
            "awards": result.get("awards", {}),
            "pot": result.get("pot", 0),
            "uncontested": result.get("uncontested", False),
            "runs": [
                {"board": run.get("board", []), "pots": run.get("pots", [])}
                for run in result.get("runs", [])
            ],
        },
    }


def _last_hand_view(state: State) -> dict[str, Any] | None:
    """The finished hand, with the undealt cards taken out.

    `last_hand` holds the board that never came so it can be rabbit hunted.
    Those cards are face down, so they must not travel: everything else
    about the hand goes out, and the rabbit cards go only through
    `_rabbit_view`, one at a time, as people turn them over.
    """
    last = state.get("last_hand")
    if last is None:
        return None
    return {key: value for key, value in last.items() if key != "rabbit"}


def _showdown_view(state: State, player_id: str | None) -> dict[str, Any] | None:
    """Who is still deciding, and whether this viewer is one of them."""
    pending = state.get("showdown")
    if not pending or not pending.get("waiting"):
        return None
    seat_number = seat_of(state, player_id) if player_id else None
    return {
        "waiting": list(pending["waiting"]),
        "yours": seat_number in pending["waiting"],
        "shown": pending.get("shown", {}),
    }


def _rabbit_view(state: State) -> dict[str, Any] | None:
    """Face-down means face down on the wire too.

    Only the cards somebody has actually turned over are sent; the rest are
    a count, so the undealt board cannot be read out of network traffic.
    """
    last = state.get("last_hand") or {}
    rabbit = last.get("rabbit")
    if not rabbit or state.get("hand"):
        return None
    revealed = set(rabbit.get("revealed", []))
    return {
        "from": rabbit["from"],
        "count": len(rabbit["cards"]),
        "cards": [
            rabbit["cards"][i] if i in revealed else None
            for i in range(len(rabbit["cards"]))
        ],
    }


def view_for(state: State, player_id: str | None, now: float | None = None) -> dict[str, Any]:
    """The snapshot one viewer is allowed to see.

    This is the only place that decides who sees what. A spectator passes
    `player_id` as None and gets the public view: no hole cards at all, ever,
    except hands turned face up at showdown.
    """
    at = time.time() if now is None else now
    hand = _load_hand(state)
    if hand is None and state.get("last_hand"):
        hand_view = _finished_hand_view(state)
    else:
        hand_view = _live_hand_view(state, hand, player_id)

    seats = []
    for number, row in sorted(state["seats"].items(), key=lambda kv: int(kv[0])):
        seat_number = int(number)
        player = state["players"].get(row["player_id"])
        if player is None:
            continue
        in_hand = hand is not None and seat_number in hand.seats
        seats.append(
            {
                "seat": seat_number,
                "player": identity.public_player(player, player_id),
                # What is in front of them right now: during a hand the chips
                # already pushed in are shown as a bet, not as stack.
                "stack": (
                    hand.seats[seat_number].stack if in_hand else row.get("stack", 0)
                ),
                "state": row.get("state", PLAYING),
                "leaving": bool(row.get("leaving")),
                **_seat_hand_view(hand, seat_number, player_id),
            }
        )

    me = state["players"].get(player_id or "")
    viewer = None
    if me is not None:
        seat_number = seat_of(state, me["id"])
        viewer = {
            "player_id": me["id"],
            "nick": me["nick"],
            "seat": seat_number,
            "held": me["held"],
            "is_host": identity.is_host(state, me["id"]),
            "can_resume_cash_out": bool(ledger.open_cash_outs(state, me["id"])),
            "auto_muck": bool(me.get("auto_muck", False)),
            # Your own notes about other people, and nobody else's: this
            # is the only place they are ever sent.
            "notes": dict(me.get("notes", {})),
            "legal": (
                hand.legal_actions(seat_number)
                if hand is not None and seat_number is not None
                and seat_number in hand.seats
                else {"actions": [], "to_call": 0}
            ),
        }

    return {
        "name": state["name"],
        "settings": state["settings"],
        "seats": seats,
        "hand": hand_view,
        # The hand just finished stays on screen through the pause before
        # the next one. The undealt cards it carries for rabbit hunting are
        # stripped out here: they go out through `rabbit` below, and only
        # once somebody has turned them over.
        "last_hand": _last_hand_view(state),
        "you": viewer,
        "messages": state.get("messages", []),
        "log": state.get("log", [])[-40:],
        "ledger": ledger.summary(state),
        "paused": state.get("paused", False),
        "ended": state.get("ended", False),
        "button": state.get("button"),
        "hand_no": state.get("hand_no", 0),
        "next_straddle": state.get("next_straddle"),
        "clock": clock.view(state, at),
        "waitlist": [
            {
                "player_id": w["player_id"],
                "nick": state["players"].get(w["player_id"], {}).get("nick", ""),
                "seat": w.get("seat"),
                "you": w["player_id"] == player_id,
            }
            for w in state.get("waitlist", [])
        ],
        "tracker": ledger.tracker_rows(state),
        # Presets are the host's own; nobody else needs the list.
        "presets": (
            sorted(state.get("presets", {}))
            if identity.is_host(state, player_id)
            else []
        ),
        "run_choice": _run_choice_view(hand),
        "showdown": _showdown_view(state, player_id),
        "equity": _equity_view(state),
        "rabbit": _rabbit_view(state),
        "reactions": [
            {"seat": r["seat"], "emoji": r["emoji"],
             "seconds_left": round(max(0.0, r["until"] - at), 1)}
            for r in state.get("reactions", [])
            if r["until"] > at
        ],
        "pending_claims": (
            [
                {"id": c["id"], "player_id": c["player_id"], "nick": c["nick"]}
                for c in state.get("pending_claims", [])
            ]
            if identity.is_host(state, player_id)
            else []
        ),
        "pending_buy_ins": (
            [
                {
                    "id": r["id"],
                    "player_id": r["player_id"],
                    "nick": state["players"].get(r["player_id"], {}).get("nick", ""),
                    "seat": r["seat"],
                    "amount": r["amount"],
                }
                for r in state.get("pending_buy_ins", [])
            ]
            if identity.is_host(state, player_id)
            else []
        ),
    }


def _live_hand_view(
    state: State, hand: holdem.Hand | None, player_id: str | None
) -> dict[str, Any] | None:
    if hand is None:
        return None
    return {
        "hand_no": hand.hand_no,
        "street": hand.street,
        "board": list(hand.board),
        "boards": [list(hand.board)],
        "pot": hand.pot_total,
        "current_bet": hand.current_bet,
        "to_act": hand.to_act,
        "button": hand.button,
        "complete": False,
    }


def _finished_hand_view(state: State) -> dict[str, Any]:
    last = state["last_hand"]
    return {
        "hand_no": last["hand_no"],
        "street": holdem.COMPLETE,
        "board": last["boards"][0] if last["boards"] else [],
        "boards": last["boards"],
        "pot": last["result"].get("pot", 0),
        "to_act": None,
        "button": last["button"],
        "complete": True,
        "result": last["result"],
    }


def _seat_hand_view(
    hand: holdem.Hand | None, seat_number: int, player_id: str | None
) -> dict[str, Any]:
    """What this viewer may see about this seat's part in the hand.

    Hole cards go to their owner and to nobody else, unless the hand was
    turned face up at showdown. Everyone else is told only that there are
    cards there.
    """
    if hand is None or seat_number not in hand.seats:
        return {"in_hand": False, "cards": None, "card_count": 0}

    seat = hand.seats[seat_number]
    mine = player_id is not None and seat.player_id == player_id
    visible = mine or seat.revealed

    view: dict[str, Any] = {
        "in_hand": not seat.folded,
        "folded": seat.folded,
        "all_in": seat.all_in,
        "committed": seat.committed,
        "card_count": len(seat.hole) if not seat.folded or seat.revealed else 0,
        "cards": list(seat.hole) if visible else None,
    }
    if mine and seat.hole and not seat.folded:
        view["hand_name"] = describe_any(seat.hole + hand.board)
    view["last_action"] = _last_action_on_street(hand, seat_number)
    return view


# The engine logs a board card at the start of each street, so everything
# after the final one happened on the street being played now.
_ACTION_KINDS = {"check", "call", "fold", "raise", "short_all_in", "blind",
                 "ante", "straddle"}


def _last_action_on_street(hand: holdem.Hand, seat_number: int) -> str | None:
    """What this seat last did on the street in progress.

    Used to show CHECK in front of a player the way a bet is shown. Only
    the current street counts: a check on the flop means nothing once the
    turn is out.
    """
    if hand.street in (holdem.COMPLETE, holdem.RUN_CHOICE):
        return None
    for entry in reversed(hand.log):
        if entry.get("kind") == "board":
            break
        if entry.get("seat") != seat_number:
            continue
        kind = entry.get("kind")
        if kind in _ACTION_KINDS:
            return "raise" if kind == "short_all_in" else kind
    return None
