"""Host controls: settings, stack edits, approvals, roles, ending the game."""

from __future__ import annotations

import pytest

from app import host, identity, ledger, table as T
from app.engine import holdem
from test_table import add_player, make_table, play_out, run, seat_players, settle_down


# ------------------------------------------------------------- settings


def test_the_host_can_change_the_blinds_from_the_next_hand():
    state, ids = seat_players(3)
    before = holdem.Hand.from_dict(state["hand"]).config.big_blind

    state = run(state, host.change_settings(ids[0], {"small_blind": 5, "big_blind": 10}))

    # The hand in progress is untouched.
    assert holdem.Hand.from_dict(state["hand"]).config.big_blind == before
    state = play_out(state)
    assert holdem.Hand.from_dict(state["hand"]).config.big_blind == 10


def test_only_the_host_can_change_settings():
    state, ids = seat_players(3)
    with pytest.raises(T.Rejected, match="Only the host"):
        host.change_settings(ids[1], {"big_blind": 10})(state)


def test_a_co_host_can_change_settings():
    state, ids = seat_players(3)
    state = run(state, host.set_co_host(ids[0], ids[1], True))
    state = run(state, host.change_settings(ids[1], {"big_blind": 6}))
    assert state["settings"]["big_blind"] == 6


def test_settings_are_checked_not_just_stored():
    state, ids = seat_players(3)

    with pytest.raises(T.Rejected, match="not a setting you can change"):
        host.change_settings(ids[0], {"rake": 10})(state)
    with pytest.raises(T.Rejected, match="must be a number"):
        host.change_settings(ids[0], {"big_blind": "lots"})(state)
    with pytest.raises(T.Rejected, match="not a valid structure"):
        host.change_settings(ids[0], {"structure": "fixed-limit-ish"})(state)
    with pytest.raises(T.Rejected, match="small blind cannot be bigger"):
        host.change_settings(ids[0], {"small_blind": 50})(state)
    with pytest.raises(T.Rejected, match="at least twice the big blind"):
        host.change_settings(ids[0], {"max_straddle": 3})(state)


def test_the_seat_count_cannot_drop_out_from_under_someone():
    state, ids = seat_players(4)
    with pytest.raises(T.Rejected, match="Someone is sitting"):
        host.change_settings(ids[0], {"seat_count": 2})(state)


def test_turning_on_pot_limit_is_enforced_from_the_next_hand():
    state, ids = seat_players(3)
    state = run(state, host.change_settings(ids[0], {"structure": "pot-limit"}))
    state = play_out(state)

    hand = holdem.Hand.from_dict(state["hand"])
    # Blinds 1/2 three-handed: the first player to act may raise to 7.
    assert hand.legal_actions(hand.to_act)["max_raise_to"] == 7


def test_a_preflop_limit_is_enforced_from_the_next_hand():
    state, ids = seat_players(3)
    state = run(state, host.change_settings(ids[0], {"preflop_limit": 15}))
    state = play_out(state)

    hand = holdem.Hand.from_dict(state["hand"])
    actions = hand.legal_actions(hand.to_act)
    assert actions["max_raise_to"] == 15
    assert actions["capped_by"] == "preflop limit"


# ---------------------------------------------------------- stack edits


def test_a_stack_edit_is_logged_and_shows_in_the_ledger():
    state, ids = seat_players(3)
    state = settle_down(state, ids[0])

    state = run(state, host.edit_stack(ids[0], ids[1], 500, "paid me in cash"))

    assert state["seats"][str(T.seat_of(state, ids[1]))]["stack"] == 500
    entry = [e for e in state["ledger"] if e["kind"] == ledger.HOST_ADJUSTMENT][-1]
    assert entry["by"] == ids[0]
    assert entry["reason"] == "paid me in cash"
    row = ledger.summary_for(state, ids[1])
    assert row["host_adjustments"] == entry["amount"]
    # The adjustment is excluded from the balance check, so it still holds.
    assert ledger.nets_balance(state)


def test_stacks_cannot_be_edited_mid_hand():
    state, ids = seat_players(3)
    assert state["hand"] is not None
    with pytest.raises(T.Rejected, match="between hands"):
        host.edit_stack(ids[0], ids[1], 500, None)(state)


def test_only_the_host_can_edit_a_stack():
    state, ids = seat_players(3)
    state = settle_down(state, ids[0])
    with pytest.raises(T.Rejected, match="Only the host"):
        host.edit_stack(ids[1], ids[2], 500, None)(state)


# ------------------------------------------------------------- the role


def test_the_host_code_lets_someone_else_take_over():
    state = make_table()
    code = identity.new_secret()
    token, link = identity.new_secret(), identity.new_secret()
    state = run(state, T.join("Host", token, link, code))
    first = next(iter(state["players"]))
    state, second, _t, _r = add_player(state, "Friend")

    with pytest.raises(T.Rejected, match="not right"):
        host.claim_host(second, "guess")(state)

    state = run(state, host.claim_host(second, code))

    assert identity.is_host(state, second)
    assert not state["players"][first]["is_host"]


def test_removing_a_player_holds_their_chips_rather_than_losing_them():
    state, ids = seat_players(3)
    state = settle_down(state, ids[0])
    before = T.chips_owned(state)

    state = run(state, host.remove_player(ids[0], ids[2]))

    assert T.seat_of(state, ids[2]) is None
    assert state["players"][ids[2]]["held"] > 0
    assert T.chips_owned(state) == before
    assert ledger.nets_balance(state)


def test_a_revoked_player_cannot_come_back():
    state, ids = seat_players(3)
    state = settle_down(state, ids[0])
    state = run(state, host.remove_player(ids[0], ids[2], revoke=True))

    with pytest.raises(T.Rejected, match="removed by the host"):
        T.sit(ids[2], 5, None, True)(state)
    assert identity.find_by_token(state, "anything") is None


def test_removing_someone_mid_hand_waits_for_the_hand_to_end():
    state, ids = seat_players(3)
    state = run(state, host.remove_player(ids[0], ids[1]))
    seat = T.seat_of(state, ids[1])
    assert seat is None or state["seats"][str(seat)].get("leaving") is True


# ----------------------------------------------------- buy-in approvals


def test_a_buy_in_can_wait_for_the_host():
    state, ids = seat_players(2, auto_approve_buy_ins=False)
    state, newcomer, _t, _r = add_player(state, "Newcomer")

    state = run(state, host.request_buy_in(newcomer, 4, 200, "req1"))
    assert T.seat_of(state, newcomer) is None
    assert len(state["pending_buy_ins"]) == 1

    state = run(state, host.resolve_buy_in(ids[0], "req1", approve=True))

    assert T.seat_of(state, newcomer) == 4
    assert state["players"][newcomer]["buy_in_total"] == 200
    assert state["pending_buy_ins"] == []


def test_a_denied_buy_in_leaves_no_trace():
    state, ids = seat_players(2, auto_approve_buy_ins=False)
    state, newcomer, _t, _r = add_player(state, "Newcomer")
    state = run(state, host.request_buy_in(newcomer, 4, 200, "req1"))

    state = run(state, host.resolve_buy_in(ids[0], "req1", approve=False))

    assert T.seat_of(state, newcomer) is None
    assert state["players"][newcomer]["buy_in_total"] == 0
    assert state["pending_buy_ins"] == []


# --------------------------------------------------------------- straddle


def test_straddling_is_off_until_the_host_sets_a_maximum():
    state, ids = seat_players(3)
    with pytest.raises(T.Rejected, match="switched off"):
        host.post_straddle(ids[0], 4)(state)


def test_a_straddle_is_posted_on_the_next_hand():
    state, ids = seat_players(3, max_straddle=10)
    straddler = ids[1]

    with pytest.raises(T.Rejected, match="between 4 and 10"):
        host.post_straddle(straddler, 3)(state)
    with pytest.raises(T.Rejected, match="between 4 and 10"):
        host.post_straddle(straddler, 20)(state)

    state = run(state, host.post_straddle(straddler, 8))
    assert state["next_straddle"]["amount"] == 8

    state = play_out(state)
    hand = holdem.Hand.from_dict(state["hand"])
    assert hand.current_bet == 8
    assert state["next_straddle"] is None, "it applies to one hand only"


# ------------------------------------------------------------- ending


def test_ending_the_game_cashes_everyone_out():
    state, ids = seat_players(4)
    state = settle_down(state, ids[0])
    bought = T.chips_bought_in(state)

    state = run(state, host.end_game(ids[0]))

    assert state["ended"] is True
    assert state["seats"] == {}
    assert all(p["held"] == 0 for p in state["players"].values())
    assert sum(p["cash_out_total"] for p in state["players"].values()) == bought
    assert ledger.nets_balance(state)


def test_a_game_cannot_be_ended_mid_hand():
    state, ids = seat_players(3)
    with pytest.raises(T.Rejected, match="Finish the hand"):
        host.end_game(ids[0])(state)


def test_nobody_can_sit_down_after_the_game_ends():
    state, ids = seat_players(2)
    state = settle_down(state, ids[0])
    state = run(state, host.end_game(ids[0]))
    with pytest.raises(T.Rejected, match="has ended"):
        T.sit(ids[0], 0, 200, False)(state)
