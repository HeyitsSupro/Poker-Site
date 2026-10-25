"""Random hands, played by bots that only ever make legal moves.

What is being checked is not that the bots play well — they play terribly —
but that the engine never loses a chip, never hands anyone a negative stack,
never pays out more or less than went in, and never gets stuck with nobody to
act. Section 9 asks for 100k hands of this.

The suite runs a few thousand by default so it stays quick. For the full run:

    POKER_FUZZ_HANDS=100000 python3 -m pytest tests/test_fuzz.py -q

The seed is fixed so a failure can be reproduced exactly; the shuffle under
test is still the real one, because each hand is dealt from a deck shuffled
by `secrets` unless a test hands one in.
"""

from __future__ import annotations

import json
import os
import random

import pytest

from app.engine.cards import shuffled_deck
from app.engine.holdem import COMPLETE, Config, Hand, IllegalAction, start_hand

DEFAULT_HANDS = int(os.environ.get("POKER_FUZZ_HANDS", "3000"))
MAX_ACTIONS = 400


def random_config(rng: random.Random) -> Config:
    structure = rng.choice(["no-limit", "no-limit", "pot-limit"])
    big_blind = rng.choice([2, 2, 2, 4, 10])
    ante_type = rng.choice(["none", "none", "none", "all", "big-blind"])
    return Config(
        small_blind=max(1, big_blind // 2),
        big_blind=big_blind,
        ante=rng.choice([0, 1, 2]) if ante_type != "none" else 0,
        ante_type=ante_type,
        structure=structure,
        preflop_limit=rng.choice([0, 0, 0, 0, big_blind * 5, big_blind * 25]),
    )


def random_table(rng: random.Random) -> tuple[dict, Config, int]:
    count = rng.randint(2, 10)
    config = random_config(rng)
    stacks = {}
    for seat in range(count):
        # A mix of deep stacks and stacks too short to cover a blind, which
        # is where the awkward cases live.
        stacks[seat] = (
            f"p{seat}",
            rng.choice(
                [
                    rng.randint(1, config.big_blind),
                    rng.randint(config.big_blind, config.big_blind * 5),
                    rng.randint(config.big_blind * 10, config.big_blind * 200),
                ]
            ),
        )

    config = _maybe_straddle(rng, config, count)
    return stacks, config, rng.randrange(count)


def _maybe_straddle(rng: random.Random, config: Config, count: int) -> Config:
    if rng.random() > 0.15:
        return config
    return Config(
        **{
            **config.to_dict(),
            "straddle": {
                "seat": rng.randrange(count),
                "amount": config.big_blind * rng.choice([2, 3, 5]),
            },
        }
    )


def pick_action(rng: random.Random, hand: Hand) -> tuple[str, int | None]:
    """Choose any legal action, favouring the ones that move chips."""
    seat_no = hand.to_act
    legal = hand.legal_actions(seat_no)
    actions = legal["actions"]
    choice = rng.choice(actions + [a for a in actions if a in ("call", "check")])

    if choice in ("bet", "raise"):
        low = legal["min_raise_to"]
        high = legal["max_raise_to"]
        if low > high:
            return "call" if "call" in actions else "check", None
        amount = rng.choice([low, high, rng.randint(low, high)])
        return choice, amount
    return choice, None


def play_one(rng: random.Random, deck: list[str] | None = None) -> Hand:
    stacks, config, button = random_table(rng)
    hand = start_hand(1, button, stacks, config, deck=deck)
    check_invariants(hand)

    for _ in range(MAX_ACTIONS):
        if hand.street == COMPLETE:
            break
        assert hand.to_act is not None, "a live hand always has someone to act"
        action, amount = pick_action(rng, hand)
        hand.act(hand.to_act, action, amount)
        check_invariants(hand)
    else:  # pragma: no cover - would mean the engine is stuck
        raise AssertionError("hand did not finish")

    check_finished(hand)
    return hand


def check_invariants(hand: Hand) -> None:
    expected = sum(hand.starting_stacks.values())
    assert hand.chips_on_table() == expected, "chips appeared or vanished"
    assert all(seat.stack >= 0 for seat in hand.seats.values()), "negative stack"
    assert all(seat.total >= 0 for seat in hand.seats.values())

    if hand.street != COMPLETE and hand.to_act is not None:
        seat = hand.seats[hand.to_act]
        assert not seat.folded and not seat.all_in, "a dead seat was asked to act"

    # Nobody can owe more than the current bet.
    for seat in hand.seats.values():
        assert seat.committed <= hand.current_bet or seat.all_in


def check_finished(hand: Hand) -> None:
    result = hand.result
    assert result is not None
    assert hand.to_act is None

    paid = sum(result["awards"].values())
    assert paid == result["pot"], "payouts do not equal the pot"
    assert sum(s.stack for s in hand.seats.values()) == sum(
        hand.starting_stacks.values()
    )

    # Every card that exists appears exactly once.
    seen = [c for s in hand.seats.values() for c in s.hole]
    seen += hand.board + hand.burned + hand.deck
    for completion in hand.run_boards:
        seen += completion
    assert len(seen) == 52, f"{len(seen)} cards accounted for"
    assert len(set(seen)) == 52, "a card was dealt twice"


# ------------------------------------------------------------------ tests


def test_random_hands_conserve_chips_and_finish():
    rng = random.Random(20261024)
    for index in range(DEFAULT_HANDS):
        try:
            play_one(rng)
        except AssertionError as exc:  # pragma: no cover - diagnostics
            raise AssertionError(f"hand {index} failed: {exc}") from exc


def test_an_illegal_amount_is_always_refused():
    """Bots that cheat get told no, and the hand carries on unchanged."""
    rng = random.Random(7)
    for _ in range(200):
        stacks, config, button = random_table(rng)
        hand = start_hand(1, button, stacks, config)
        if hand.street == COMPLETE:
            continue
        before = json.dumps(hand.to_dict())
        legal = hand.legal_actions(hand.to_act)
        if "raise" in legal["actions"] or "bet" in legal["actions"]:
            too_much = legal["max_raise_to"] + 1
            with pytest.raises(IllegalAction):
                hand.act(hand.to_act, "raise", too_much)
            assert json.dumps(hand.to_dict()) == before, "a refusal changed state"


def test_saving_and_reloading_mid_hand_changes_nothing():
    """Section 9: save at random points, reload, and get the same hand.

    The hand is played twice from the same deck with the same choices. The
    second time it is frozen to JSON and thawed again after every single
    action, the way a real server does between actions.
    """
    for seed in range(300):
        rng_a = random.Random(seed)
        rng_b = random.Random(seed)
        deck = shuffled_deck()

        straight_through = play_one(rng_a, deck=list(deck))

        stacks, config, button = random_table(rng_b)
        interrupted = start_hand(1, button, stacks, config, deck=list(deck))
        for _ in range(MAX_ACTIONS):
            if interrupted.street == COMPLETE:
                break
            action, amount = pick_action(rng_b, interrupted)
            interrupted.act(interrupted.to_act, action, amount)
            # The server dies here and comes back.
            interrupted = Hand.from_dict(json.loads(json.dumps(interrupted.to_dict())))

        assert interrupted.to_dict() == straight_through.to_dict(), (
            f"seed {seed}: restoring changed the hand"
        )
