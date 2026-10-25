"""Phase 4: the things that happen because time passed, or because a player
chose something other than a bet.

Run-it-more, live win percentages, rabbit hunting, show-or-muck, the action
clock and the time bank, and emoji reactions.

Like the other table tests these drive the pure mutate functions, so a
thirty-second clock costs nothing to run out.
"""

from __future__ import annotations

import pytest

from app import clock, identity, table as T
from app.engine import holdem
from app.engine.cards import make_deck


def run(state, mutate):
    new_state, _events = mutate(state)
    return new_state


def run_with_events(state, mutate):
    return mutate(state)


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


def seat_to_act(state):
    hand = hand_of(state)
    return None if hand is None or hand.to_act is None else hand.to_act


def player_to_act(state):
    hand = hand_of(state)
    return hand.seats[hand.to_act].player_id


def act(state, action, amount=None):
    return run(state, T.act(player_to_act(state), action, amount))


def tick_past(state, pad=1.0):
    """Let whatever countdown is running expire."""
    deadline = state.get("deadline")
    assert deadline is not None, "no countdown was running"
    return run(state, T.tick(now=deadline["at"] + pad))


# ----------------------------------------------------------- action clock


def test_a_clock_starts_for_whoever_is_to_act():
    state, _ids = seat_players(3, action_seconds=30)
    countdown = clock.get(state)
    assert countdown is not None
    assert countdown["kind"] == clock.ACTION
    assert countdown["seat"] == seat_to_act(state)
    assert countdown["phase"] == clock.PHASE_CLOCK


def test_the_clock_is_not_restarted_by_someone_elses_chat():
    state, ids = seat_players(3, action_seconds=30)
    before = clock.get(state)["at"]
    # A player who is not to act says something.
    speaker = next(p for p in ids if p != player_to_act(state))
    state = run(state, T.say(speaker, "nice hand"))
    assert clock.get(state)["at"] == before, "the clock must not be topped up"


def test_the_clock_moves_to_the_next_player_after_an_action():
    state, _ids = seat_players(3, action_seconds=30)
    first = seat_to_act(state)
    state = act(state, "fold")
    assert seat_to_act(state) != first
    assert clock.get(state)["seat"] == seat_to_act(state)


def test_running_out_of_time_spends_the_time_bank_first():
    state, _ids = seat_players(3, action_seconds=30, time_bank_seconds=60)
    seat = seat_to_act(state)

    state = tick_past(state)

    countdown = clock.get(state)
    assert countdown["phase"] == clock.PHASE_BANK
    assert countdown["seat"] == seat, "still their turn; they are on the bank"
    assert seat_to_act(state) == seat


def test_running_out_of_the_bank_too_folds_or_checks():
    state, _ids = seat_players(3, action_seconds=30, time_bank_seconds=60)
    seat = seat_to_act(state)

    state = tick_past(state)   # into the time bank
    state = tick_past(state)   # out of it

    hand = hand_of(state)
    # Preflop there is always a blind to call, so timing out is a fold.
    assert hand is None or hand.seats[seat].folded
    assert seat_to_act(state) != seat


def test_a_free_check_is_taken_rather_than_folding():
    """Timing out must never throw away a hand that costs nothing to keep."""
    state, _ids = seat_players(2, action_seconds=30, time_bank_seconds=0)
    state = act(state, "call")      # small blind completes
    state = act(state, "check")     # big blind checks: now the flop
    assert hand_of(state).street == holdem.FLOP

    seat = seat_to_act(state)
    state = tick_past(state)

    hand = hand_of(state)
    assert not hand.seats[seat].folded, "checking was free, so it checked"


def test_the_host_can_switch_the_clock_off():
    state, _ids = seat_players(3, action_seconds=0)
    assert clock.get(state) is None


def test_an_away_player_does_not_hold_the_table_up():
    state, ids = seat_players(3, action_seconds=30)
    victim = player_to_act(state)
    seat = seat_to_act(state)

    state = run(state, T.set_away(victim, True))

    assert seat_to_act(state) != seat, "the action moved on without them"


# -------------------------------------------------------- between hands


def test_there_is_a_pause_before_the_next_hand():
    state, _ids = seat_players(3, between_hands_seconds=6)
    first = state["hand_no"]
    for _ in range(40):
        if state.get("hand") is None:
            break
        state = act(state, "fold")

    assert state.get("hand") is None, "the hand is over"
    assert state["hand_no"] == first, "but the next one has not been dealt"
    assert clock.get(state)["kind"] == clock.NEXT_HAND

    state = tick_past(state)
    assert state["hand_no"] == first + 1


def test_no_pause_means_the_next_hand_is_immediate():
    state, _ids = seat_players(3, between_hands_seconds=0)
    first = state["hand_no"]
    for _ in range(40):
        if state["hand_no"] != first:
            break
        state = act(state, "fold")
    assert state["hand_no"] == first + 1


# ------------------------------------------------------------ run it more


def all_in_heads_up(**settings):
    """Two players, both all in before the board is out."""
    state, ids = seat_players(2, buy_in=100, **settings)
    state = act(state, "raise", 100)
    state = act(state, "call")
    return state, ids


def test_an_all_in_with_board_to_come_asks_how_many_runs():
    state, _ids = all_in_heads_up(max_runs=3)
    hand = hand_of(state)
    assert hand.street == holdem.RUN_CHOICE
    assert sorted(hand.run_voters()) == [0, 1]
    assert clock.get(state)["kind"] == clock.RUN_CHOICE


def test_the_lowest_number_anybody_asks_for_is_used():
    state, ids = all_in_heads_up(max_runs=3)
    state = run(state, T.choose_runs(ids[0], 3))
    state = run(state, T.choose_runs(ids[1], 2))

    # Everyone has chosen, so it starts without waiting for the timer.
    assert state.get("hand") is None or hand_of(state).street != holdem.RUN_CHOICE
    boards = state["last_hand"]["boards"]
    assert len(boards) == 2


def test_the_runs_start_as_soon_as_everyone_has_confirmed():
    state, ids = all_in_heads_up(max_runs=2)
    state = run(state, T.choose_runs(ids[0], 2))
    assert hand_of(state).street == holdem.RUN_CHOICE, "still waiting on the other"
    state = run(state, T.choose_runs(ids[1], 2))
    assert state["last_hand"] is not None


def test_the_timer_settles_it_when_somebody_does_not_choose():
    state, ids = all_in_heads_up(max_runs=3)
    state = run(state, T.choose_runs(ids[0], 3))

    state = tick_past(state)

    # Nothing chosen counts as one, and the lowest wins, so one run.
    assert len(state["last_hand"]["boards"]) == 1


def test_choosing_more_than_the_maximum_is_refused():
    state, ids = all_in_heads_up(max_runs=2)
    with pytest.raises(T.Rejected):
        run(state, T.choose_runs(ids[0], 5))


def test_someone_not_in_the_hand_gets_no_say():
    state, ids = seat_players(3, buy_in=100, max_runs=3)
    folder = player_to_act(state)
    state = act(state, "fold")
    state = act(state, "raise", 100)
    state = act(state, "call")
    assert hand_of(state).street == holdem.RUN_CHOICE

    with pytest.raises(T.Rejected):
        run(state, T.choose_runs(folder, 2))


def test_every_run_shares_the_board_already_dealt():
    state, ids = seat_players(2, buy_in=100, max_runs=2)
    state = act(state, "call")
    state = act(state, "check")
    assert hand_of(state).street == holdem.FLOP
    flop = list(hand_of(state).board)

    state = act(state, "check")
    state = act(state, "raise", 98)
    state = act(state, "call")

    state = run(state, T.choose_runs(ids[0], 2))
    state = run(state, T.choose_runs(ids[1], 2))

    boards = state["last_hand"]["boards"]
    assert len(boards) == 2
    assert boards[0][:3] == flop and boards[1][:3] == flop
    assert boards[0][3:] != boards[1][3:], "the rest of each board differs"


def test_chips_are_conserved_across_several_runs():
    state, ids = seat_players(2, buy_in=100, max_runs=3)
    before = T.chips_owned(state)
    state = act(state, "raise", 100)
    state = act(state, "call")
    state = run(state, T.choose_runs(ids[0], 3))
    state = run(state, T.choose_runs(ids[1], 3))
    assert T.chips_owned(state) == before


# ------------------------------------------------------ win percentages


def test_percentages_appear_while_the_board_is_still_to_come():
    """Both all in, hands on their backs, waiting on the run-it-more vote."""
    state, ids = all_in_heads_up(max_runs=3)
    assert hand_of(state).street == holdem.RUN_CHOICE

    odds = T.view_for(state, ids[0])["equity"]
    assert odds is not None
    assert set(odds) == {"0", "1"}
    # A split is a tie for both players, so it is counted once, not twice:
    # heads up, the two win percentages plus the shared tie make a whole.
    assert odds["0"]["tie"] == pytest.approx(odds["1"]["tie"], abs=0.2)
    total = odds["0"]["win"] + odds["1"]["win"] + odds["0"]["tie"]
    assert total == pytest.approx(100.0, abs=1.0)


def test_an_instant_run_out_still_records_the_standing():
    """One run deals the whole board at once, so the numbers are kept."""
    state, _ids = all_in_heads_up(max_runs=1)
    standing = state["last_hand"]["result"]["equity_at_all_in"]
    assert set(standing["seats"]) == {"0", "1"}
    assert standing["board"] == [], "they were all in before the flop"


def test_percentages_never_come_from_a_hand_nobody_can_see():
    """A folded player's cards must not be in the calculation."""
    state, ids = seat_players(3, buy_in=100, max_runs=3)
    state = act(state, "fold")
    state = act(state, "raise", 100)
    state = act(state, "call")

    odds = T.view_for(state, ids[0])["equity"]
    assert odds is not None and len(odds) == 2, "only the two still in"


def test_percentages_are_not_shown_before_anyone_is_face_up():
    state, ids = seat_players(3)
    assert T.view_for(state, ids[0])["equity"] is None


def test_a_percentage_is_only_recomputed_when_it_could_have_changed():
    state, ids = all_in_heads_up(max_runs=1)
    first = state["equity"]
    again = run(state, T.say(ids[0], "hello"))
    assert again["equity"] is first or again["equity"] == first


# --------------------------------------------------------- rabbit hunting


def fold_everyone_out(state):
    for _ in range(40):
        if state.get("hand") is None:
            return state
        state = act(state, "fold")
    raise AssertionError("the hand never ended")


def test_the_cards_that_never_came_are_offered_face_down():
    state, ids = seat_players(3, rabbit_hunting=True, between_hands_seconds=30)
    state = fold_everyone_out(state)

    view = T.view_for(state, ids[0])
    rabbit = view["rabbit"]
    assert rabbit is not None
    assert rabbit["count"] == 5, "nothing was dealt, so the whole board is left"
    assert rabbit["cards"] == [None] * 5, "and all of it is face down"


def test_flipping_a_card_shows_it_to_the_whole_table():
    state, ids = seat_players(3, rabbit_hunting=True, between_hands_seconds=30)
    state = fold_everyone_out(state)

    state = run(state, T.reveal(ids[1], 0))

    for viewer in ids:
        cards = T.view_for(state, viewer)["rabbit"]["cards"]
        assert cards[0] is not None, "everyone sees it"
        assert cards[1:] == [None] * 4, "and only that one"


def test_a_face_down_rabbit_card_is_not_in_the_snapshot():
    state, ids = seat_players(3, rabbit_hunting=True, between_hands_seconds=30)
    state = fold_everyone_out(state)
    hidden = state["last_hand"]["rabbit"]["cards"]

    import json

    view = T.view_for(state, ids[0])
    # Every position is still face down, and the cards themselves are not
    # anywhere in the snapshot. The quotes matter: a bare "7c" could turn
    # up inside a random player id by chance, but a JSON string value
    # cannot contain a quote, so `"7c"` only matches a real card.
    assert view["rabbit"]["cards"] == [None] * len(hidden)
    sent = json.dumps(view)
    for card in hidden:
        assert f'"{card}"' not in sent, "an unflipped card must not reach the client"


def test_rabbit_hunting_is_refused_while_a_hand_is_live():
    state, ids = seat_players(3, rabbit_hunting=True)
    with pytest.raises(T.Rejected):
        run(state, T.reveal(ids[0], 0))


def test_rabbit_hunting_can_be_switched_off():
    state, ids = seat_players(3, rabbit_hunting=False, between_hands_seconds=30)
    state = fold_everyone_out(state)
    assert T.view_for(state, ids[0])["rabbit"] is None
    with pytest.raises(T.Rejected):
        run(state, T.reveal(ids[0], 0))


def test_the_same_card_cannot_be_flipped_twice():
    state, ids = seat_players(3, rabbit_hunting=True, between_hands_seconds=30)
    state = fold_everyone_out(state)
    state = run(state, T.reveal(ids[0], 2))
    with pytest.raises(T.Rejected):
        run(state, T.reveal(ids[1], 2))


# ------------------------------------------------------------ show or muck


def check_it_down(state):
    """Take the hand that is live now to showdown without anybody betting."""
    this_hand = (state.get("hand") or {}).get("hand_no")
    for _ in range(24):
        data = state.get("hand")
        if data is None or data["hand_no"] != this_hand:
            return state
        hand = holdem.Hand.from_dict(data)
        if hand.to_act is None:
            return state
        legal = hand.legal_actions(hand.to_act)
        action = "check" if "check" in legal["actions"] else "call"
        state = act(state, action)
    raise AssertionError("the hand never reached showdown")


def showdown_state(**settings):
    """Two players check a hand down to a showdown where one may muck.

    Whether anybody gets the choice depends on the cards, so this deals
    until it gets a hand that poses the question rather than skipping.
    """
    settings.setdefault("showdown_seconds", 10)
    settings.setdefault("between_hands_seconds", 30)
    for _ in range(40):
        state, ids = seat_players(2, buy_in=200, **settings)
        state = check_it_down(state)
        if (state.get("showdown") or {}).get("waiting"):
            return state, ids
    raise AssertionError("never dealt a hand with an optional muck")


def test_a_beaten_hand_may_be_mucked():
    state, ids = showdown_state()
    pending = state["showdown"]
    assert pending["waiting"], "somebody has a decision"

    seat = pending["waiting"][0]
    player_id = state["seats"][str(seat)]["player_id"]
    state = run(state, T.show_or_muck(player_id, []))

    assert not (state.get("showdown") or {}).get("waiting")


def test_showing_puts_the_cards_in_the_result():
    state, ids = showdown_state()
    pending = state["showdown"]
    seat = pending["waiting"][0]
    player_id = state["seats"][str(seat)]["player_id"]
    state = run(state, T.show_or_muck(player_id, [0, 1]))

    shown = state["last_hand"]["result"].get("volunteered", {})
    assert str(seat) in shown and len(shown[str(seat)]) == 2


def test_only_one_card_can_be_shown():
    state, ids = showdown_state()
    pending = state["showdown"]
    seat = pending["waiting"][0]
    player_id = state["seats"][str(seat)]["player_id"]
    state = run(state, T.show_or_muck(player_id, [1]))

    shown = state["last_hand"]["result"]["volunteered"]
    assert len(shown[str(seat)]) == 1


def test_not_deciding_in_time_mucks_the_losing_hand():
    state, ids = showdown_state()
    assert clock.get(state)["kind"] == clock.SHOWDOWN

    state = tick_past(state)

    assert not (state.get("showdown") or {}).get("waiting")
    assert state["last_hand"]["result"].get("volunteered") in (None, {})


def test_auto_muck_never_holds_the_table_up():
    state, ids = seat_players(2, buy_in=200, showdown_seconds=10,
                              between_hands_seconds=0)
    for player_id in ids:
        state = run(state, T.set_auto_muck(player_id, True))
    state = check_it_down(state)
    assert state.get("showdown") is None, "nobody was asked"


def test_an_all_in_showdown_turns_everything_face_up():
    """No more betting is possible, so there is nothing to protect."""
    state, _ids = all_in_heads_up(max_runs=1)
    assert state["last_hand"] is not None
    shown = state["last_hand"]["result"]["shown"]
    assert len(shown) == 2, "both hands face up automatically"
    assert state.get("showdown") is None, "and nobody was asked to decide"


def test_a_player_cannot_muck_for_somebody_else():
    state, ids = showdown_state()
    pending = state["showdown"]
    seat = pending["waiting"][0]
    other = next(
        p for p in ids if p != state["seats"][str(seat)]["player_id"]
    )
    with pytest.raises(T.Rejected):
        run(state, T.show_or_muck(other, []))


# ------------------------------------------------------------- reactions


def test_a_reaction_shows_up_beside_the_seat():
    state, ids = seat_players(3)
    state = run(state, T.react(ids[0], "👍", now=1000.0))
    shown = T.view_for(state, ids[1], now=1000.5)["reactions"]
    assert len(shown) == 1
    assert shown[0]["emoji"] == "👍"
    assert shown[0]["seat"] == 0


def test_a_reaction_fades():
    state, ids = seat_players(3)
    state = run(state, T.react(ids[0], "🎉", now=1000.0))
    later = T.view_for(state, ids[1], now=1000.0 + T.REACTION_SECONDS + 1)
    assert later["reactions"] == []


def test_only_the_offered_reactions_are_accepted():
    state, ids = seat_players(3)
    with pytest.raises(T.Rejected):
        run(state, T.react(ids[0], "not an emoji"))


# ---------------------------------------------------- timers and restarts


def test_a_countdown_survives_being_saved_and_reloaded():
    import json

    state, _ids = seat_players(3, action_seconds=30)
    reloaded = json.loads(json.dumps(state))
    assert clock.get(reloaded) == clock.get(state)
    # And it still expires correctly on the other side of the restart.
    expired = run(reloaded, T.tick(now=clock.get(reloaded)["at"] + 1))
    assert clock.get(expired)["phase"] == clock.PHASE_BANK


def test_a_tick_that_is_not_due_changes_nothing():
    state, _ids = seat_players(3, action_seconds=30)
    before = dict(state)
    after = run(state, T.tick(now=clock.get(state)["at"] - 5))
    assert after["hand_no"] == before["hand_no"]
    assert clock.get(after)["at"] == clock.get(state)["at"]
