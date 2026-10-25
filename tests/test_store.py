"""Persistence: save after every action, survive a restart, ignore replays."""

from __future__ import annotations

import asyncio
import json

import pytest

from app import identity, table as table_model
from app.db import Database
from app.store import Event, TableNotFound, TableStore
from conftest import TEST_DATABASE_URL, needs_db

pytestmark = needs_db


def count_to(value: int):
    def mutate(state):
        return {**state, "counter": value}, [Event("set", {"counter": value})]

    return mutate


async def test_create_and_load(store, table_id):
    await store.create_table(table_model.new_state("Friday game"), table_id)
    state, seq = await store.load(table_id)
    assert state["name"] == "Friday game"
    assert seq == 0


async def test_unknown_table_is_reported(store):
    with pytest.raises(TableNotFound):
        await store.load("not-a-real-table")


async def test_every_action_is_saved_and_logged(store, table_id):
    await store.create_table(table_model.new_state(), table_id)

    for n in range(1, 6):
        result = await store.apply_action(table_id, f"a{n}", count_to(n))
        assert result.seq == n
        assert result.duplicate is False
        # The write is committed before the caller is told, so a crash on the
        # very next line could not lose it.
        state, seq = await store.load(table_id)
        assert state["counter"] == n
        assert seq == n

    events = await store.events_since(table_id, 0)
    assert [e["kind"] for e in events] == ["set"] * 5
    assert [e["payload"]["counter"] for e in events] == [1, 2, 3, 4, 5]


async def test_a_replayed_action_changes_nothing(store, table_id):
    """A reconnect that re-sends the same action must not apply it twice."""
    await store.create_table(table_model.new_state(), table_id)
    await store.apply_action(table_id, "once", count_to(7))

    again = await store.apply_action(table_id, "once", count_to(99))

    assert again.duplicate is True
    assert again.state["counter"] == 7
    assert again.seq == 1
    state, seq = await store.load(table_id)
    assert state["counter"] == 7 and seq == 1
    assert len(await store.events_since(table_id, 0)) == 1


async def test_concurrent_actions_are_serialised(store, table_id):
    """Two clients acting at once must not interleave into a lost update."""
    await store.create_table({**table_model.new_state(), "counter": 0}, table_id)

    def increment(state):
        value = state["counter"] + 1
        return {**state, "counter": value}, [Event("inc", {"counter": value})]

    await asyncio.gather(
        *(store.apply_action(table_id, f"c{i}", increment) for i in range(20))
    )

    state, seq = await store.load(table_id)
    assert state["counter"] == 20
    assert seq == 20


async def test_a_rejected_action_leaves_no_trace(store, table_id):
    """If the mutate function refuses, nothing is written at all."""
    await store.create_table(table_model.new_state(), table_id)
    await store.apply_action(table_id, "ok", count_to(3))

    def refuses(_state):
        raise ValueError("not your turn")

    with pytest.raises(ValueError):
        await store.apply_action(table_id, "bad", refuses)

    state, seq = await store.load(table_id)
    assert state["counter"] == 3 and seq == 1
    # The action id was not burned either, so a corrected retry can reuse it.
    result = await store.apply_action(table_id, "bad", count_to(4))
    assert result.duplicate is False


async def test_state_survives_a_server_restart(table_id):
    """A hand in progress survives the process dying mid-hand.

    This is the headline guarantee of section 4.2: the same player, the same
    hole cards, the same deck. Nothing is re-dealt.
    """
    first = Database(TEST_DATABASE_URL)
    store_a = TableStore(first)
    await store_a.ensure_schema()
    await store_a.create_table(table_model.new_state("Restart me"), table_id)

    tokens = []
    for index in range(2):
        token, rejoin = identity.new_secret(), identity.new_secret()
        tokens.append(token)
        await store_a.apply_action(
            table_id, f"join{index}", table_model.join(f"P{index}", token, rejoin)
        )
        state, _ = await store_a.load(table_id)
        player_id = identity.find_by_token(state, token)
        await store_a.apply_action(
            table_id,
            f"sit{index}",
            table_model.sit(player_id, index, 200, resume=False),
        )

    state, seq_before = await store_a.load(table_id)
    assert state["hand"] is not None, "a hand is in progress"
    hand_before = state["hand"]
    await first.close()  # the process dies here, mid-hand

    second = Database(TEST_DATABASE_URL)
    store_b = TableStore(second)
    try:
        state, seq = await store_b.load(table_id)
        assert seq == seq_before
        # The same cards, the same deck, the same player to act.
        assert state["hand"] == hand_before
        for index, token in enumerate(tokens):
            assert identity.find_by_token(state, token) is not None
        assert table_id in await store_b.list_open_tables()
    finally:
        await second.close()


async def test_hand_history_is_append_only(store, table_id):
    await store.create_table(table_model.new_state(), table_id)
    await store.append_hand(table_id, 1, {"board": ["Ad", "5h", "As"]})
    await store.append_hand(table_id, 1, {"board": ["overwritten?"]})
    row = await store.db.fetchrow(
        "SELECT data FROM hands WHERE table_id = $1 AND hand_no = 1", table_id
    )
    assert json.loads(row["data"])["board"] == ["Ad", "5h", "As"]


async def test_closed_tables_drop_out_of_the_resume_list(store, table_id):
    await store.create_table(table_model.new_state(), table_id)
    assert table_id in await store.list_open_tables()
    await store.close_table(table_id)
    assert table_id not in await store.list_open_tables()


async def test_only_tables_with_a_live_countdown_are_listed(store, table_id):
    """Start-up must not read back every open table to find the timers."""
    import time as _time

    quiet = f"{table_id}-quiet"
    await store.create_table(table_model.new_state("No clock"), quiet)
    await store.create_table(table_model.new_state("Ticking"), table_id)

    due_at = _time.time() + 30

    def start_clock(state):
        state = {**state, "deadline": {"kind": "action", "at": due_at}}
        return state, [Event("clock", {})]

    await store.apply_action(table_id, "clock", start_clock)

    pending = dict(await store.tables_with_deadlines())
    assert table_id in pending
    assert quiet not in pending, "a table with no countdown is not read back"
    assert pending[table_id] == pytest.approx(due_at, abs=0.01)


async def test_a_table_with_a_null_deadline_is_not_listed(store, table_id):
    await store.create_table(table_model.new_state(), table_id)
    # `new_state` sets the key to null rather than leaving it out.
    pending = dict(await store.tables_with_deadlines())
    assert table_id not in pending
