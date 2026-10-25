"""Nothing hidden may leave the server.

Section 4.4 and section 9 ask for a test that inspects outgoing messages and
proves no hidden information leaks. Now that there are real cards, that
means three things, checked against the actual bytes a socket receives:

* a player never sees another player's hole cards before they are shown;
* a spectator never sees any hole card at all;
* the undealt deck never leaves the server in any form.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import identity, table as T
from app.engine import holdem
from app.main import app
from conftest import needs_db

pytestmark = needs_db

ORIGIN = {"origin": "https://example.github.io"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def dump(messages) -> str:
    return json.dumps(messages)


def cards_in(text: str, cards: list[str]) -> list[str]:
    """Which of these cards appear as card values in the text."""
    return [card for card in cards if f'"{card}"' in text]


# ------------------------------------------------- the pure view function


def seated_table(count=3):
    """A table with everyone seated and a hand dealt."""
    state = T.new_state("Leak test")
    state, _ = T.join("P0", identity.new_secret(), identity.new_secret())(state)
    host_id = next(iter(state["players"]))
    state, _ = T.pause(host_id, True)(state)
    state, _ = T.sit(host_id, 0, 200, False)(state)
    ids = [host_id]
    for index in range(1, count):
        state, _ = T.join(f"P{index}", identity.new_secret(), identity.new_secret())(
            state
        )
        player_id = [p for p in state["players"] if p not in ids][0]
        state, _ = T.sit(player_id, index, 200, False)(state)
        ids.append(player_id)
    state, _ = T.pause(host_id, False)(state)
    return state, ids


def test_a_view_carries_only_the_viewers_own_cards():
    state, ids = seated_table(3)
    hand = holdem.Hand.from_dict(state["hand"])

    for viewer in ids:
        text = dump(T.view_for(state, viewer))
        seat_number = T.seat_of(state, viewer)
        mine = hand.seats[seat_number].hole
        theirs = [
            card
            for seat in hand.seats.values()
            if seat.seat != seat_number
            for card in seat.hole
        ]
        assert cards_in(text, mine) == mine, "a player sees their own cards"
        assert cards_in(text, theirs) == [], "and nobody else's"


def test_a_view_never_carries_the_deck():
    state, ids = seated_table(3)
    hand = holdem.Hand.from_dict(state["hand"])

    for viewer in [None, *ids]:
        text = dump(T.view_for(state, viewer))
        assert cards_in(text, hand.deck) == [], "the undealt deck stayed home"
        assert cards_in(text, hand.burned) == [], "so did the burn cards"


def test_a_spectator_sees_no_hole_card_at_all():
    state, _ids = seated_table(3)
    hand = holdem.Hand.from_dict(state["hand"])
    every_hole = [c for seat in hand.seats.values() for c in seat.hole]

    text = dump(T.view_for(state, None))

    assert cards_in(text, every_hole) == []
    assert '"cards": null' in text or '"cards":null' in text


def test_no_secret_hash_is_ever_in_a_view():
    state, ids = seated_table(2)
    for viewer in [None, *ids]:
        text = dump(T.view_for(state, viewer))
        assert "token_hash" not in text
        assert "rejoin_hash" not in text
        assert "host_code_hash" not in text


def test_showdown_turns_hands_face_up_for_everyone():
    """What is shown at showdown is public, and only then."""
    state, ids = seated_table(2)
    hand = holdem.Hand.from_dict(state["hand"])
    before = dump(T.view_for(state, None))
    everyones = [c for seat in hand.seats.values() for c in seat.hole]
    assert cards_in(before, everyones) == []

    # Check it down to the river.
    for _ in range(40):
        data = state.get("hand")
        if not data or data["hand_no"] != hand.hand_no:
            break
        live = holdem.Hand.from_dict(data)
        if live.to_act is None:
            break
        legal = live.legal_actions(live.to_act)
        action = "check" if "check" in legal["actions"] else "call"
        state, _ = T.act(live.seats[live.to_act].player_id, action, None)(state)

    after = dump(T.view_for(state, None))
    if state.get("last_hand") and not state["last_hand"]["result"]["uncontested"]:
        assert cards_in(after, everyones), "a contested showdown is public"


# ------------------------------------------------------- over the socket


def test_one_players_cards_never_reach_another_player(client):
    table_id = client.post("/api/tables", json={}).json()["table_id"]

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ana:
        ana.send_json({"t": "hello", "protocol": 1, "nick": "Ana"})
        ana.receive_json()
        ana.receive_json()

        with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ben:
            ben.send_json({"t": "hello", "protocol": 1, "nick": "Ben"})
            seen_by_ben = [ben.receive_json(), ben.receive_json()]
            ana.receive_json()

            ana.send_json(
                {"t": "action", "aid": "s1", "kind": "sit", "data": {"seat": 0}}
            )
            ana.receive_json()
            seen_by_ana = [ana.receive_json()]
            seen_by_ben.append(ben.receive_json())

            ben.send_json(
                {"t": "action", "aid": "s2", "kind": "sit", "data": {"seat": 1}}
            )
            ben.receive_json()
            seen_by_ben.append(ben.receive_json())
            seen_by_ana.append(ana.receive_json())

            ana_view = seen_by_ana[-1]["state"]
            ben_view = seen_by_ben[-1]["state"]
            assert ana_view["hand"] is not None, "a hand is running"

            ana_cards = next(
                s["cards"] for s in ana_view["seats"] if s["player"]["you"]
            )
            ben_cards = next(
                s["cards"] for s in ben_view["seats"] if s["player"]["you"]
            )
            assert ana_cards and ben_cards and set(ana_cards) != set(ben_cards)

            # Now the real check: the actual bytes each socket received.
            assert cards_in(dump(seen_by_ben), ana_cards) == []
            assert cards_in(dump(seen_by_ana), ben_cards) == []


def test_a_spectator_receives_nothing_private(client):
    table_id = client.post("/api/tables", json={}).json()["table_id"]
    sockets = []

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ana:
        ana.send_json({"t": "hello", "protocol": 1, "nick": "Ana"})
        ana.receive_json(), ana.receive_json()
        with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ben:
            ben.send_json({"t": "hello", "protocol": 1, "nick": "Ben"})
            ben.receive_json(), ben.receive_json()
            ana.receive_json()

            with client.websocket_connect(
                f"/ws?table={table_id}", headers=ORIGIN
            ) as watcher:
                watcher.send_json({"t": "hello", "protocol": 1, "spectate": True})
                welcome = watcher.receive_json()
                sockets.append(welcome)
                sockets.append(watcher.receive_json())
                assert welcome["player_id"] is None

                for socket, seat in ((ana, 0), (ben, 1)):
                    socket.send_json(
                        {
                            "t": "action",
                            "aid": f"sit{seat}",
                            "kind": "sit",
                            "data": {"seat": seat},
                        }
                    )
                    socket.receive_json()
                    socket.receive_json()
                    sockets.append(watcher.receive_json())
                ana.receive_json()

                text = dump(sockets)
                last = sockets[-1]["state"]
                assert last["hand"] is not None
                assert all(s["cards"] is None for s in last["seats"])
                assert "token" not in text or '"token": null' in text

    # A spectator cannot act either.
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as watcher:
        watcher.send_json({"t": "hello", "protocol": 1, "spectate": True})
        watcher.receive_json(), watcher.receive_json()
        watcher.send_json({"t": "action", "aid": "x", "kind": "say", "data": {}})
        assert watcher.receive_json()["code"] == "spectator"
