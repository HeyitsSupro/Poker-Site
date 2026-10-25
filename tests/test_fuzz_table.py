"""A whole session, fuzzed: people joining, leaving, busting, coming back.

The engine fuzz in test_fuzz.py proves one hand never loses a chip. This
proves the table around it never does either — across sitting down, standing
up, cashing out, resuming, going away and busting out — and that the ledger
still adds up at the end, which is section 9's "ledger and identity" check.
"""

from __future__ import annotations

import os
import random

from app import identity, ledger, table as T
from app.engine import holdem

SESSIONS = int(os.environ.get("POKER_TABLE_FUZZ_SESSIONS", "60"))
STEPS = int(os.environ.get("POKER_TABLE_FUZZ_STEPS", "150"))


def run(state, mutate):
    new_state, _events = mutate(state)
    return new_state


# The table module owns this arithmetic, so the test checks the real thing
# rather than a second implementation that could drift from it.
owned = T.chips_owned
bought_in = T.chips_bought_in


def play_a_turn(state, rng):
    """If a hand is waiting on someone, make a legal move for them."""
    data = state.get("hand")
    if not data:
        return state
    hand = holdem.Hand.from_dict(data)
    if hand.to_act is None:
        return state
    legal = hand.legal_actions(hand.to_act)
    actions = legal["actions"]
    choice = rng.choice(actions + [a for a in actions if a in ("check", "call")])
    amount = None
    if choice in ("bet", "raise"):
        low, high = legal["min_raise_to"], legal["max_raise_to"]
        if low > high:
            choice = "call" if "call" in actions else "check"
        else:
            amount = rng.randint(low, high)
    player_id = hand.seats[hand.to_act].player_id
    return run(state, T.act(player_id, choice, amount))


def test_a_fuzzed_session_never_loses_a_chip():
    for seed in range(SESSIONS):
        rng = random.Random(seed)
        # Kept so a failure says which move broke the invariant, not just
        # that something did.
        history: list[str] = []
        state = T.new_state("Fuzz", {"seat_count": 6})

        # The host joins first and keeps the table paused between fiddles so
        # the structural changes land between hands, as they would in play.
        state = run(state, T.join("Host", identity.new_secret(), identity.new_secret()))
        host = next(iter(state["players"]))
        players = [host]

        for _ in range(STEPS):
            choice = rng.random()

            label = "noop"
            if choice < 0.55:
                label = "play a turn"
                state = play_a_turn(state, rng)

            elif choice < 0.68 and len(players) < 6:
                label = "join"
                state = run(
                    state,
                    T.join(
                        f"P{len(players)}",
                        identity.new_secret(),
                        identity.new_secret(),
                    ),
                )
                players = list(state["players"])

            elif choice < 0.85:
                # Someone with no seat tries to sit down.
                seated = {s["player_id"] for s in state["seats"].values()}
                free = [p for p in players if p not in seated]
                taken = {int(n) for n in state["seats"]}
                chairs = [n for n in range(6) if n not in taken]
                if free and chairs:
                    player_id = rng.choice(free)
                    held = state["players"][player_id]["held"]
                    cashed = ledger.open_cash_outs(state, player_id)
                    seat = rng.choice(chairs)
                    try:
                        if held and rng.random() < 0.7:
                            label = "sit, resuming a held stack"
                            state = run(state, T.sit(player_id, seat, None, True))
                        elif cashed and rng.random() < 0.5:
                            label = "sit, resuming a cash-out"
                            state = run(state, T.resume_cash_out(player_id, seat))
                        else:
                            label = "sit, buying in"
                            state = run(state, T.sit(player_id, seat, 200, False))
                    except T.Rejected:
                        pass

            elif choice < 0.93:
                seated = [s["player_id"] for s in state["seats"].values()]
                if seated:
                    try:
                        label = "stand up"
                        state = run(state, T.stand_up(rng.choice(seated)))
                    except T.Rejected:
                        pass

            elif choice < 0.97:
                standing = [
                    p
                    for p in players
                    if T.seat_of(state, p) is None
                    and state["players"][p]["held"] > 0
                ]
                if standing:
                    try:
                        label = "cash out"
                        state = run(state, T.cash_out(rng.choice(standing)))
                    except T.Rejected:
                        pass

            else:
                label = "pause"
                state = run(state, T.pause(host, not state["paused"]))

            history.append(label)
            assert owned(state) == bought_in(state), (
                f"seed {seed}: chips do not match the buy-ins after "
                f"{label!r} (owned {owned(state)}, bought in {bought_in(state)}); "
                f"last moves: {history[-6:]}"
            )
            assert all(
                seat.get("stack", 0) >= 0 for seat in state["seats"].values()
            ), f"seed {seed}: negative stack"
            assert all(
                p["held"] >= 0 for p in state["players"].values()
            ), f"seed {seed}: negative held stack"

        assert ledger.nets_balance(state), f"seed {seed}: ledger does not balance"


def test_resuming_never_counts_as_a_buy_in():
    """However the chips move, only a real buy-in moves the counters."""
    for seed in range(40):
        rng = random.Random(1000 + seed)
        state = T.new_state("Counters", {"seat_count": 4})
        state = run(state, T.join("A", identity.new_secret(), identity.new_secret()))
        host = next(iter(state["players"]))
        state = run(state, T.pause(host, True))
        state = run(state, T.sit(host, 0, 200, False))

        expected_count, expected_total = 1, 200

        for _ in range(30):
            seat = T.seat_of(state, host)
            try:
                if seat is not None:
                    state = run(state, T.stand_up(host))
                elif state["players"][host]["held"] > 0:
                    if rng.random() < 0.5:
                        state = run(state, T.sit(host, 0, None, True))
                    else:
                        state = run(state, T.cash_out(host))
                elif ledger.open_cash_outs(state, host):
                    state = run(state, T.resume_cash_out(host, 0))
                else:
                    state = run(state, T.sit(host, 0, 200, False))
                    expected_count += 1
                    expected_total += 200
            except T.Rejected:
                continue

            player = state["players"][host]
            assert player["buy_in_count"] == expected_count
            assert player["buy_in_total"] == expected_total
