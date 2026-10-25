"""Phase 5: the records, the waitlist, presets, notes and the export.

The one that matters most here is the hand-history redaction. A stored
hand holds every hole card and the rest of the deck; what a player may
read back afterwards is only what they could have seen at the time.
"""

from __future__ import annotations

import json

import pytest

from app import host as host_controls, identity, ledger, table as T
from app.engine import holdem


def run(state, mutate):
    new_state, _events = mutate(state)
    return new_state


def make_table(**settings):
    return T.new_state("Test table", settings)


def add_player(state, nick):
    token, rejoin = identity.new_secret(), identity.new_secret()
    state = run(state, T.join(nick, token, rejoin))
    return state, identity.find_by_token(state, token)


def seat_players(count=3, buy_in=200, **settings):
    state = make_table(**settings)
    state, host = add_player(state, "P0")
    state = run(state, T.pause(host, True))
    state = run(state, T.sit(host, 0, buy_in, resume=False))
    ids = [host]
    for index in range(1, count):
        state, player_id = add_player(state, f"P{index}")
        state = run(state, T.sit(player_id, index, buy_in, resume=False))
        ids.append(player_id)
    state = run(state, T.pause(host, False))
    return state, ids


def hand_of(state):
    data = state.get("hand")
    return holdem.Hand.from_dict(data) if data else None


def act(state, action, amount=None):
    hand = hand_of(state)
    return run(state, T.act(hand.seats[hand.to_act].player_id, action, amount))


def calm(state, host):
    """End the hand in progress and stop another from being dealt.

    Standing up mid-hand only marks a player as leaving, so anything that
    tests what happens after they go has to let the hand finish first.
    """
    state = run(state, T.pause(host, True))
    for _ in range(40):
        data = state.get("hand")
        if data is None:
            return state
        hand = holdem.Hand.from_dict(data)
        if hand.to_act is None:
            if state.get("deadline"):
                state = run(state, T.tick(now=state["deadline"]["at"] + 1))
                continue
            return state
        state = act(state, "fold")
    raise AssertionError("the hand never ended")


def play_to_showdown(state):
    """Check a hand down, returning the stored hand dict and the state."""
    this_hand = state["hand"]["hand_no"]
    stored = None
    for _ in range(30):
        data = state.get("hand")
        if data is None or data["hand_no"] != this_hand:
            break
        hand = holdem.Hand.from_dict(data)
        if hand.to_act is None:
            if state.get("deadline"):
                state = run(state, T.tick(now=state["deadline"]["at"] + 1))
                continue
            break
        legal = hand.legal_actions(hand.to_act)
        state = act(state, "check" if "check" in legal["actions"] else "call")
    return state


# ------------------------------------------------------- history redaction


def finished_hand(**settings):
    """Play one hand out and return (state, the stored hand, player ids)."""
    settings.setdefault("between_hands_seconds", 60)
    settings.setdefault("showdown_seconds", 0)
    state, ids = seat_players(3, **settings)
    captured = {}

    # The stored hand is what _finish_hand hands to the history table.
    original = T._finish_hand

    def capture(st, hand):
        events = original(st, hand)
        for event in events:
            if event.kind == "hand_history":
                captured["hand"] = event.payload["hand"]
        return events

    T._finish_hand = capture
    try:
        state = play_to_showdown(state)
    finally:
        T._finish_hand = original
    assert "hand" in captured, "no hand reached the history"
    return state, captured["hand"], ids


def test_the_whole_hand_is_stored_including_what_is_secret():
    """The archive keeps everything; redaction happens on the way out."""
    _state, stored, _ids = finished_hand()
    assert stored["deck"], "the rest of the deck is kept"
    assert all(seat["hole"] for seat in stored["seats"].values())


def test_you_can_read_back_your_own_cards():
    state, stored, ids = finished_hand()
    replay = T.replay_for(stored, state, ids[0])
    mine = next(s for s in replay["seats"] if s["you"])
    assert mine["cards"] and len(mine["cards"]) == 2


def test_a_mucked_hand_stays_mucked():
    """Otherwise anyone could read back what everyone folded all night."""
    state, stored, ids = finished_hand()
    shown = set((stored["result"].get("shown") or {}).keys())

    replay = T.replay_for(stored, state, ids[0])
    for seat in replay["seats"]:
        if seat["you"] or str(seat["seat"]) in shown:
            continue
        assert seat["cards"] is None, "a hand nobody saw is not handed back"


def test_a_hand_shown_at_showdown_is_readable_by_everyone():
    state, stored, ids = finished_hand()
    shown = (stored["result"].get("shown") or {})
    if not shown:
        pytest.skip("this hand ended before a showdown")

    # A spectator, with no player id at all.
    replay = T.replay_for(stored, state, None)
    readable = {str(s["seat"]) for s in replay["seats"] if s["cards"]}
    assert readable == set(shown)


def test_the_deck_never_leaves_in_a_replay():
    state, stored, ids = finished_hand()
    for viewer in [None, *ids]:
        text = json.dumps(T.replay_for(stored, state, viewer))
        assert "deck" not in text
        # Every card still in the deck must be absent from the replay.
        for card in stored["deck"]:
            assert f'"{card}"' not in text, f"{card} leaked into the replay"


def test_a_replay_has_the_board_the_betting_and_the_payouts():
    state, stored, ids = finished_hand()
    replay = T.replay_for(stored, state, ids[0])
    assert replay["hand_no"] == stored["hand_no"]
    assert replay["boards"]
    assert replay["steps"], "the hand plays back step by step"
    assert "awards" in replay["result"]
    assert all("nick" in seat for seat in replay["seats"])


# --------------------------------------------------------------- waitlist


def test_waiting_for_a_seat_gets_you_one_when_it_frees_up():
    state, ids = seat_players(2, seat_count=2, between_hands_seconds=0)
    state = calm(state, ids[0])
    state, waiting = add_player(state, "Late")

    state = run(state, T.join_waitlist(waiting))
    assert T.seat_of(state, waiting) is None, "the table is full"
    assert len(state["waitlist"]) == 1

    # Somebody leaves, and the seat goes to whoever was waiting.
    state = run(state, T.stand_up(ids[1]))

    assert T.seat_of(state, waiting) is not None, "they were seated"
    assert state["waitlist"] == []


def test_asking_for_a_particular_seat_waits_for_that_one():
    state, ids = seat_players(2, seat_count=4, between_hands_seconds=0)
    state = calm(state, ids[0])
    state, waiting = add_player(state, "Picky")

    state = run(state, T.join_waitlist(waiting, seat=0))

    # Seats 2 and 3 are free, but they asked for 0, which is taken.
    assert T.seat_of(state, waiting) is None
    state = run(state, T.stand_up(ids[0]))
    assert T.seat_of(state, waiting) == 0


def test_a_waiting_player_is_not_seated_mid_hand():
    state, ids = seat_players(2, seat_count=4, between_hands_seconds=60)
    state, waiting = add_player(state, "Late")
    assert state.get("hand"), "a hand is running"

    state = run(state, T.join_waitlist(waiting))

    assert T.seat_of(state, waiting) is None
    assert len(state["waitlist"]) == 1


def test_you_can_take_yourself_off_the_waitlist():
    state, ids = seat_players(2, seat_count=2)
    state, waiting = add_player(state, "Late")
    state = run(state, T.join_waitlist(waiting))
    state = run(state, T.leave_waitlist(waiting))
    assert state["waitlist"] == []
    with pytest.raises(T.Rejected):
        run(state, T.leave_waitlist(waiting))


def test_the_waitlist_is_visible_to_everyone():
    state, ids = seat_players(2, seat_count=2)
    state, waiting = add_player(state, "Late")
    state = run(state, T.join_waitlist(waiting))

    view = T.view_for(state, ids[0])
    assert [w["nick"] for w in view["waitlist"]] == ["Late"]
    assert view["waitlist"][0]["you"] is False
    assert T.view_for(state, waiting)["waitlist"][0]["you"] is True


# ------------------------------------------------------------ player notes


def test_a_note_is_only_ever_sent_to_its_author():
    state, ids = seat_players(3)
    state = run(state, T.set_note(ids[0], ids[1], "calls far too much"))

    mine = T.view_for(state, ids[0])
    assert mine["you"]["notes"][ids[1]] == "calls far too much"

    for other in (ids[1], ids[2], None):
        text = json.dumps(T.view_for(state, other))
        assert "calls far too much" not in text


def test_a_note_is_not_written_to_the_table_log():
    state, ids = seat_players(3)
    state = run(state, T.set_note(ids[0], ids[1], "bluffs the river"))
    assert "bluffs the river" not in json.dumps(state["log"])


def test_clearing_a_note_removes_it():
    state, ids = seat_players(3)
    state = run(state, T.set_note(ids[0], ids[1], "something"))
    state = run(state, T.set_note(ids[0], ids[1], ""))
    assert T.view_for(state, ids[0])["you"]["notes"] == {}


def test_you_cannot_keep_a_note_about_yourself():
    state, ids = seat_players(3)
    with pytest.raises(T.Rejected):
        run(state, T.set_note(ids[0], ids[0], "handsome"))


# ---------------------------------------------------------------- presets


def test_a_host_can_save_and_load_a_preset():
    state, ids = seat_players(2, small_blind=1, big_blind=2)
    state = run(state, host_controls.save_preset(ids[0], "Friday"))
    assert "Friday" in state["presets"]

    state = run(
        state, host_controls.change_settings(ids[0], {"small_blind": 5, "big_blind": 10})
    )
    assert state["settings"]["big_blind"] == 10

    state = run(state, host_controls.load_preset(ids[0], "Friday"))
    assert state["settings"]["big_blind"] == 2


def test_a_loaded_preset_is_still_checked():
    """A preset saved under old rules must not slip past validation."""
    state, ids = seat_players(2, seat_count=4)
    state = run(state, host_controls.save_preset(ids[0], "Small"))
    # Doctor the saved preset into something impossible.
    state["presets"]["Small"]["small_blind"] = 500
    state["presets"]["Small"]["big_blind"] = 2

    with pytest.raises(T.Rejected):
        run(state, host_controls.load_preset(ids[0], "Small"))


def test_only_the_host_sees_or_saves_presets():
    state, ids = seat_players(2)
    state = run(state, host_controls.save_preset(ids[0], "Friday"))
    assert T.view_for(state, ids[0])["presets"] == ["Friday"]
    assert T.view_for(state, ids[1])["presets"] == []
    with pytest.raises(T.Rejected):
        run(state, host_controls.save_preset(ids[1], "Mine"))


def test_a_preset_can_be_deleted():
    state, ids = seat_players(2)
    state = run(state, host_controls.save_preset(ids[0], "Friday"))
    state = run(state, host_controls.delete_preset(ids[0], "Friday"))
    assert state["presets"] == {}


# ---------------------------------------------------------------- rathole


def test_you_cannot_buy_back_in_short_while_the_rathole_rule_is_on():
    state, ids = seat_players(2, rathole_minutes=30, min_buy_in=40,
                              max_buy_in=400, between_hands_seconds=0)
    state = calm(state, ids[0])
    state = run(state, T.stand_up(ids[1]))
    held = state["players"][ids[1]]["held"]
    assert held > 0

    state = run(state, T.cash_out(ids[1]))
    with pytest.raises(T.Rejected):
        run(state, T.sit(ids[1], 1, 40, resume=False))


def test_the_rathole_rule_is_off_by_default():
    state, ids = seat_players(2, between_hands_seconds=0)
    state = calm(state, ids[0])
    state = run(state, T.stand_up(ids[1]))
    state = run(state, T.cash_out(ids[1]))
    state = run(state, T.sit(ids[1], 1, 40, resume=False))
    assert T.seat_of(state, ids[1]) == 1


def test_resuming_your_own_chips_is_never_ratholing():
    state, ids = seat_players(2, rathole_minutes=30, between_hands_seconds=0)
    state = calm(state, ids[0])
    state = run(state, T.stand_up(ids[1]))
    state = run(state, T.sit(ids[1], 1, None, resume=True))
    assert T.seat_of(state, ids[1]) == 1


# -------------------------------------------------------- session summary


def test_the_session_summary_is_in_the_owners_tracker_format():
    state, ids = seat_players(3, buy_in=200)
    rows = ledger.tracker_rows(state)

    assert len(rows) == 3
    for row in rows:
        assert set(row) == {"nick", "buy_in", "count", "pnl", "notes"}
        assert row["buy_in"] == 200
        assert row["count"] == 1
        assert row["notes"] == ""
    assert sum(row["pnl"] for row in rows) == 0, "nets sum to zero"


def test_resuming_a_held_stack_does_not_add_a_buy_in_to_the_summary():
    state, ids = seat_players(2, buy_in=200, between_hands_seconds=0)
    state = calm(state, ids[0])
    state = run(state, T.stand_up(ids[1]))
    state = run(state, T.sit(ids[1], 1, None, resume=True))

    row = next(r for r in ledger.tracker_rows(state) if r["nick"] == "P1")
    assert row["count"] == 1, "still one buy-in"
    assert row["buy_in"] == 200


def test_the_summary_is_in_the_view_for_everyone():
    state, ids = seat_players(2)
    assert len(T.view_for(state, ids[1])["tracker"]) == 2
