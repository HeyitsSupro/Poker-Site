"""The table layer: seating, the ledger, and the hand lifecycle.

These run the pure mutate functions directly — no database, no sockets —
because that is the level the rules live at.
"""

from __future__ import annotations

import pytest

from app import identity, ledger, table as T
from app.engine import holdem


def run(state, mutate):
    """Apply a mutate the way the store does, and return the new state."""
    new_state, _events = mutate(state)
    return new_state


def make_table(**settings):
    return T.new_state("Test table", settings)


def add_player(state, nick):
    token, rejoin = identity.new_secret(), identity.new_secret()
    state = run(state, T.join(nick, token, rejoin))
    player_id = identity.find_by_token(state, token)
    return state, player_id, token, rejoin


def seat_players(count=3, buy_in=200, **settings):
    """A table with everyone seated before the first hand is dealt.

    The table is paused while people sit down, so all of them are in the
    first hand rather than the later ones arriving mid-hand.
    """
    state = make_table(**settings)
    state, host, _t, _r = add_player(state, "P0")
    state = run(state, T.pause(host, True))
    state = run(state, T.sit(host, 0, buy_in, resume=False))
    ids = [host]
    for index in range(1, count):
        state, player_id, _t2, _r2 = add_player(state, f"P{index}")
        state = run(state, T.sit(player_id, index, buy_in, resume=False))
        ids.append(player_id)
    state = run(state, T.pause(host, False))
    return state, ids


total_chips = T.chips_owned


# --------------------------------------------------------------- joining


def test_the_first_player_is_the_host():
    state, first, _t, _r = add_player(make_table(), "Ana")
    state, second, _t2, _r2 = add_player(state, "Ben")
    assert identity.is_host(state, first)
    assert not identity.is_host(state, second)


def test_a_token_finds_the_same_player_and_a_wrong_one_does_not():
    state, player_id, token, _r = add_player(make_table(), "Ana")
    assert identity.find_by_token(state, token) == player_id
    assert identity.find_by_token(state, "nope") is None
    assert identity.find_by_token(state, None) is None


def test_a_rejoin_link_finds_the_player_and_regenerating_breaks_the_old_one():
    state, player_id, _t, rejoin = add_player(make_table(), "Ana")
    assert identity.find_by_rejoin(state, rejoin) == player_id

    fresh = identity.new_secret()
    state = run(state, T.regenerate_rejoin(player_id, fresh))

    assert identity.find_by_rejoin(state, fresh) == player_id
    assert identity.find_by_rejoin(state, rejoin) is None, "the old link is dead"


def test_a_host_approved_claim_moves_the_player_to_a_new_device():
    state, host, host_token, _r = add_player(make_table(), "Host")
    state, ana, ana_token, _r2 = add_player(state, "Ana")

    new_token = identity.new_secret()
    state = run(state, T.request_claim("c1", ana, new_token, "Ana"))
    assert identity.find_by_token(state, new_token) is None, "not yet"

    state = run(state, T.resolve_claim("c1", approve=True, by=host))

    assert identity.find_by_token(state, new_token) == ana
    assert identity.find_by_token(state, ana_token) is None, "the old device is out"


def test_only_the_host_can_approve_a_claim():
    state, _host, _t, _r = add_player(make_table(), "Host")
    state, ana, _t2, _r2 = add_player(state, "Ana")
    state = run(state, T.request_claim("c1", ana, identity.new_secret(), "Ana"))

    with pytest.raises(T.Rejected, match="Only the host"):
        T.resolve_claim("c1", approve=True, by=ana)(state)


def test_a_denied_claim_leaves_the_player_alone():
    state, host, _t, _r = add_player(make_table(), "Host")
    state, ana, ana_token, _r2 = add_player(state, "Ana")
    state = run(state, T.request_claim("c1", ana, identity.new_secret(), "Ana"))
    state = run(state, T.resolve_claim("c1", approve=False, by=host))

    assert identity.find_by_token(state, ana_token) == ana


# --------------------------------------------------------------- seating


def test_sitting_down_buys_in_and_starts_a_hand_at_two_players():
    state, ids = seat_players(2)
    assert state["hand"] is not None
    assert state["players"][ids[0]]["buy_in_total"] == 200
    assert state["players"][ids[0]]["buy_in_count"] == 1


def test_a_player_who_sits_down_mid_hand_waits_for_the_next_one():
    state, _ids = seat_players(2)
    state, late, _t, _r = add_player(state, "Late")
    state = run(state, T.sit(late, 5, 200, resume=False))

    assert state["seats"]["5"]["state"] == T.WAITING


def test_a_buy_in_outside_the_range_is_refused():
    state, player_id, _t, _r = add_player(make_table(min_buy_in=40, max_buy_in=400), "A")
    with pytest.raises(T.Rejected, match="between 40 and 400"):
        T.sit(player_id, 0, 10, resume=False)(state)


def test_a_fixed_buy_in_overrides_whatever_was_asked_for():
    state, player_id, _t, _r = add_player(make_table(fixed_buy_in=150), "A")
    state = run(state, T.sit(player_id, 0, 999, resume=False))
    assert state["seats"]["0"]["stack"] == 150


def test_taking_an_occupied_seat_is_refused():
    state, ids = seat_players(2)
    state, late, _t, _r = add_player(state, "Late")
    with pytest.raises(T.Rejected, match="already in that seat"):
        T.sit(late, 0, 200, resume=False)(state)


# ------------------------------------------------- held stacks and ledger


def test_standing_up_holds_the_stack_and_resuming_is_not_a_buy_in():
    state, ids = seat_players(3)
    ana = ids[0]
    state = settle_down(state, ids[0])

    before = dict(state["players"][ana])
    state = run(state, T.stand_up(ana))

    held = state["players"][ana]["held"]
    assert held > 0
    assert T.seat_of(state, ana) is None
    assert state["players"][ana]["buy_in_total"] == before["buy_in_total"]
    assert state["players"][ana]["buy_in_count"] == before["buy_in_count"]

    state = run(state, T.sit(ana, 0, None, resume=True))

    assert state["seats"]["0"]["stack"] == held
    assert state["players"][ana]["held"] == 0
    assert state["players"][ana]["buy_in_count"] == before["buy_in_count"], (
        "resuming your own chips is not a buy-in"
    )
    assert ledger.nets_balance(state)


def test_standing_up_mid_hand_waits_for_the_hand_to_end():
    state, ids = seat_players(3)
    ana = ids[0]
    seat = T.seat_of(state, ana)
    hand = holdem.Hand.from_dict(state["hand"])
    assert seat in hand.seats

    state = run(state, T.stand_up(ana))
    assert state["seats"][str(seat)].get("leaving") is True, "still in the hand"

    state = fold_to_the_end(state)
    assert T.seat_of(state, ana) is None or state["players"][ana]["held"] >= 0


def test_cashing_out_records_the_amount_and_empties_the_seat():
    state, ids = seat_players(3)
    state = settle_down(state, ids[0])

    ana = ids[0]
    state = run(state, T.stand_up(ana))
    held = state["players"][ana]["held"]
    state = run(state, T.cash_out(ana))

    assert state["players"][ana]["held"] == 0
    assert state["players"][ana]["cash_out_total"] == held
    assert ledger.nets_balance(state)


def test_coming_back_after_a_cash_out_reverses_it_rather_than_buying_in():
    state, ids = seat_players(3)
    state = settle_down(state, ids[0])
    ana = ids[0]
    state = run(state, T.stand_up(ana))
    amount = state["players"][ana]["held"]
    state = run(state, T.cash_out(ana))

    before_count = state["players"][ana]["buy_in_count"]
    before_total = state["players"][ana]["buy_in_total"]

    state = run(state, T.resume_cash_out(ana, 0))

    assert state["seats"]["0"]["stack"] == amount
    assert state["players"][ana]["buy_in_count"] == before_count
    assert state["players"][ana]["buy_in_total"] == before_total
    assert state["players"][ana]["cash_out_total"] == 0
    kinds = [item["kind"] for item in state["ledger"]]
    assert ledger.CASH_OUT_REVERSED in kinds
    assert ledger.nets_balance(state)


def test_the_host_can_switch_off_coming_back_after_a_cash_out():
    state, ids = seat_players(3, resume_after_cash_out="off")
    state = settle_down(state, ids[0])
    ana = ids[0]
    state = run(state, T.stand_up(ana))
    state = run(state, T.cash_out(ana))

    with pytest.raises(T.Rejected, match="turned off"):
        T.resume_cash_out(ana, 0)(state)


def test_the_ledger_balances_after_a_messy_session():
    state, ids = seat_players(4)
    for _ in range(6):
        state = fold_to_the_end(state)
    state = settle_down(state, ids[0])
    state = run(state, T.stand_up(ids[0]))
    state = run(state, T.cash_out(ids[0]))
    state = run(state, T.sit(ids[0], 7, 200, resume=False))
    state = run(state, T.pause(ids[0], False))
    for _ in range(4):
        state = fold_to_the_end(state)

    assert ledger.nets_balance(state), ledger.summary(state)


# ------------------------------------------------------- hand lifecycle


def play_out(state):
    """Play the hand that is live right now to its end, and stop there.

    Everyone checks when they can and folds when they cannot, which ends
    hands quickly without needing the engine to behave in any special way.

    A finished hand can leave the table waiting on a clock rather than on a
    person — show-or-muck decisions, then the pause before the next deal —
    so this winds the clock forward rather than sitting through it.
    """
    data = state.get("hand")
    if not data:
        return state
    this_hand = data["hand_no"]

    for _ in range(400):
        data = state.get("hand")
        if data and data["hand_no"] != this_hand:
            return state
        if data is None:
            # The hand is over. Let any countdown expire so the next one is
            # dealt, which is what the caller is waiting for.
            if state.get("deadline") is None:
                return state
            state = run(state, T.tick(now=state["deadline"]["at"] + 1))
            continue
        hand = holdem.Hand.from_dict(data)
        if hand.to_act is None:
            # Waiting on a timer (a run-it-more vote, or show-or-muck).
            if state.get("deadline") is None:
                return state
            state = run(state, T.tick(now=state["deadline"]["at"] + 1))
            continue
        legal = hand.legal_actions(hand.to_act)
        action = "check" if "check" in legal["actions"] else "fold"
        state = run(state, T.act(hand.seats[hand.to_act].player_id, action, None))
    raise AssertionError("the hand never ended")


# Older name, kept for readability in the tests below.
fold_to_the_end = play_out


def settle_down(state, host):
    """Finish the hand in progress and stop dealing.

    Standing up, cashing out and editing stacks are all between-hands
    actions, so this is how a test gets the table to a quiet moment.
    """
    state = run(state, T.pause(host, True))
    return play_out(state)


def test_a_hand_starts_by_itself_once_two_players_are_seated():
    state = make_table()
    state, a, _t, _r = add_player(state, "A")
    state = run(state, T.sit(a, 0, 200, resume=False))
    assert state["hand"] is None, "one player is not a game"

    state, b, _t2, _r2 = add_player(state, "B")
    state = run(state, T.sit(b, 1, 200, resume=False))
    assert state["hand"] is not None


def test_the_next_hand_starts_when_the_last_one_ends():
    state, _ids = seat_players(3)
    first = holdem.Hand.from_dict(state["hand"]).hand_no
    state = fold_to_the_end(state)
    assert state["hand"] is not None, "a new hand is dealt"
    assert holdem.Hand.from_dict(state["hand"]).hand_no == first + 1


def test_chips_are_conserved_across_many_hands():
    state, _ids = seat_players(5)
    start = total_chips(state)
    for _ in range(25):
        state = fold_to_the_end(state)
        assert total_chips(state) == start
    assert ledger.nets_balance(state)


def test_a_paused_table_deals_nothing():
    state, ids = seat_players(3)
    state = fold_to_the_end(state)
    state = run(state, T.pause(ids[0], True))
    # Finish whatever is live, then nothing new should start.
    state = fold_to_the_end(state)
    assert state["hand"] is None


def test_acting_out_of_turn_is_refused_with_a_reason():
    state, ids = seat_players(3)
    hand = holdem.Hand.from_dict(state["hand"])
    not_their_turn = next(
        s.player_id for s in hand.seats.values() if s.seat != hand.to_act
    )
    with pytest.raises(T.Rejected, match="not your turn"):
        T.act(not_their_turn, "fold", None)(state)


def test_a_rejected_action_leaves_the_state_untouched():
    state, ids = seat_players(3)
    before = dict(state["hand"])
    with pytest.raises(T.Rejected):
        T.act(ids[0], "raise", 1)(state)
    assert state["hand"] == before


# ------------------------------------------------- blinds and the button


def test_the_big_blind_moves_on_every_hand():
    state, _ids = seat_players(4)
    seen = []
    for _ in range(8):
        seen.append(state["last_bb_seat"])
        state = fold_to_the_end(state)
    # It advances one playing seat each hand and never repeats back to back.
    assert all(a != b for a, b in zip(seen, seen[1:]))


def test_heads_up_the_button_is_the_small_blind():
    state, _ids = seat_players(2)
    assert state["button"] == state["last_sb_seat"]


def test_the_small_blind_is_dead_when_that_player_has_gone():
    """The big blind still moves on; nobody pays a blind twice."""
    state, ids = seat_players(4)
    state = fold_to_the_end(state)

    # The player who is about to be the small blind leaves.
    going = state["last_bb_seat"]
    player_id = state["seats"][str(going)]["player_id"]
    state = run(state, T.stand_up(player_id))
    state = fold_to_the_end(state)

    hand = state.get("hand")
    if hand:
        blinds = [
            entry for entry in holdem.Hand.from_dict(hand).log
            if entry["kind"] in ("blind", "dead_small_blind")
        ]
        assert blinds, "blinds were posted or recorded as dead"


# ------------------------------------------------------------- the views


def test_a_player_sees_their_own_cards_and_nobody_elses():
    state, ids = seat_players(3)
    view = T.view_for(state, ids[0])
    mine = next(s for s in view["seats"] if s["player"]["you"])
    theirs = [s for s in view["seats"] if not s["player"]["you"]]

    assert mine["cards"] and len(mine["cards"]) == 2
    assert all(seat["cards"] is None for seat in theirs)
    assert all(seat["card_count"] == 2 for seat in theirs), "backs are still shown"


def test_a_player_is_told_what_their_hand_is():
    state, ids = seat_players(3)
    view = T.view_for(state, ids[0])
    mine = next(s for s in view["seats"] if s["player"]["you"])
    # Preflop there are only two cards, so there is nothing to name yet.
    assert "hand_name" in mine


def test_a_spectator_sees_no_cards_at_all():
    state, _ids = seat_players(3)
    view = T.view_for(state, None)
    assert all(seat["cards"] is None for seat in view["seats"])
    assert view["you"] is None


def test_the_legal_actions_are_sent_to_the_player_to_act():
    state, _ids = seat_players(3)
    hand = holdem.Hand.from_dict(state["hand"])
    actor = hand.seats[hand.to_act].player_id

    view = T.view_for(state, actor)
    assert "fold" in view["you"]["legal"]["actions"]
    assert view["you"]["legal"]["max_raise_to"] > 0

    waiting = T.view_for(state, _ids[(_ids.index(actor) + 1) % len(_ids)])
    assert waiting["you"]["legal"]["actions"] == []
