"""Deadlines: the action clock, the run-it-more vote, the showdown pause.

Three things in the build instructions happen because time passed rather
than because somebody clicked: a player times out and is checked or folded,
the ten seconds to choose a number of runs run out, and a player who must
show their hand does not. All three are modelled the same way.

The table keeps at most one deadline at a time:

    {"kind": ..., "at": <epoch seconds>, "hand_no": n, ...}

`at` is stored as epoch seconds because it is arithmetic, not a label. It
never leaves the server as an absolute time: `seconds_left` is computed when
each snapshot is built, so a phone whose clock is wrong still sees the right
countdown.

Nothing here reaches for the clock itself. The caller passes `now` in, which
is what lets the save/restore tests replay a hand at any speed they like.
"""

from __future__ import annotations

from typing import Any

State = dict[str, Any]

ACTION = "action"
RUN_CHOICE = "run_choice"
SHOWDOWN = "showdown"
NEXT_HAND = "next_hand"

# While the clock is on the player's own time; after that, their time bank.
PHASE_CLOCK = "clock"
PHASE_BANK = "bank"


def make(kind: str, at: float, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "at": float(at), **extra}


def get(state: State) -> dict[str, Any] | None:
    return state.get("deadline")


def set_on(state: State, deadline: dict[str, Any] | None) -> None:
    state["deadline"] = deadline


def clear(state: State) -> None:
    state["deadline"] = None


def is_due(deadline: dict[str, Any] | None, now: float) -> bool:
    return deadline is not None and now >= deadline["at"]


def seconds_left(deadline: dict[str, Any] | None, now: float) -> float | None:
    if deadline is None:
        return None
    return max(0.0, round(deadline["at"] - now, 1))


def next_wake(state: State) -> float | None:
    """When the server should look at this table again, if ever."""
    deadline = get(state)
    return deadline["at"] if deadline else None


def view(state: State, now: float) -> dict[str, Any] | None:
    """The countdown as a client should see it."""
    deadline = get(state)
    if deadline is None:
        return None
    return {
        "kind": deadline["kind"],
        "seconds_left": seconds_left(deadline, now),
        "seat": deadline.get("seat"),
        "phase": deadline.get("phase"),
    }
