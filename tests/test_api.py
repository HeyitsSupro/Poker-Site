"""The HTTP and WebSocket surface, end to end against a real database."""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import needs_db

pytestmark = needs_db

ORIGIN = {"origin": "https://example.github.io"}
_ids = itertools.count(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_table(client, name="Test table") -> str:
    return client.post("/api/tables", json={"name": name}).json()["table_id"]


def hello(ws, nick="Ana", token=None, rejoin=None, spectate=False):
    ws.send_json(
        {
            "t": "hello",
            "protocol": 1,
            "nick": nick,
            "token": token,
            "rejoin": rejoin,
            "spectate": spectate,
        }
    )
    welcome = ws.receive_json()
    assert welcome["t"] == "welcome", welcome
    state = ws.receive_json()
    assert state["t"] == "state", state
    return welcome, state


def do(ws, kind, **data):
    """Send an action and collect the ack and the state that follows."""
    aid = f"a{next(_ids)}"
    ws.send_json({"t": "action", "aid": aid, "kind": kind, "data": data})
    ack = ws.receive_json()
    if ack["t"] == "error":
        return ack, None
    state = ws.receive_json()
    return ack, state


def drain(ws, count):
    return [ws.receive_json() for _ in range(count)]


# ------------------------------------------------------------------- HTTP


def test_health_answers_and_reports_the_database(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["ready"] is True
    assert body["heartbeat_seconds"] > 0


def test_health_is_200_even_when_the_database_is_down(client, monkeypatch):
    """Render's health check must pass while Neon is still resuming."""
    import app.main as main

    monkeypatch.setattr(main, "_db_probe", (0.0, False))

    async def asleep() -> bool:
        return False

    monkeypatch.setattr(main, "db_is_up", asleep)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ready"] is False


def test_create_and_look_up_a_table(client):
    table_id = make_table(client, "Friday")
    assert client.get(f"/api/tables/{table_id}").json()["name"] == "Friday"
    assert client.get("/api/tables/nope").status_code == 404


def test_a_foreign_origin_is_refused(client):
    table_id = make_table(client)
    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/ws?table={table_id}", headers={"origin": "https://evil.example"}
        ):
            pass


# --------------------------------------------------------------- identity


def test_joining_gives_a_token_and_a_rejoin_link_once(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        welcome, state = hello(ws)
        assert welcome["token"] and welcome["rejoin"]
        assert welcome["token"] != welcome["rejoin"]
        assert state["state"]["name"] == "Test table"


def test_reconnecting_with_the_token_is_the_same_player(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        welcome, _ = hello(ws, "Ana")

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        again, state = hello(ws, "Someone else", token=welcome["token"])
        assert again["player_id"] == welcome["player_id"]
        assert "token" not in again, "no new token for a returning device"
        assert state["state"]["you"]["nick"] == "Ana"


def test_a_rejoin_link_moves_the_player_to_a_new_device(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as first:
        welcome, _ = hello(first, "Ana")

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as second:
        moved, _ = hello(second, "Ana", rejoin=welcome["rejoin"])
        assert moved["player_id"] == welcome["player_id"]
        assert moved["token"] != welcome["token"], "the new device gets its own"

    # The old device's token no longer works: one active device per player.
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as third:
        fresh, _ = hello(third, "Ana", token=welcome["token"])
        assert fresh["player_id"] != welcome["player_id"]


def test_a_bad_rejoin_link_is_refused(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws, "Ana")

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        ws.send_json({"t": "hello", "protocol": 1, "nick": "Thief", "rejoin": "nope"})
        assert ws.receive_json()["code"] == "bad_rejoin"


def test_a_wrong_token_never_lands_on_someone_elses_player(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        first, _ = hello(ws, "Ana")

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        second, _ = hello(ws, "Ana", token="not-the-real-token")
        assert second["player_id"] != first["player_id"]


def test_a_new_rejoin_link_replaces_the_old_one(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        welcome, _ = hello(ws, "Ana")
        ws.send_json({"t": "action", "aid": "r1", "kind": "new_rejoin_link"})
        ack = ws.receive_json()
        assert ack["t"] == "ack"
        ws.receive_json()  # the state broadcast
        issued = ws.receive_json()
        assert issued["t"] == "rejoin_link"
        assert issued["rejoin"] != welcome["rejoin"]


# ------------------------------------------------------------- playing


def test_two_players_can_sit_down_and_a_hand_starts(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ana:
        hello(ana, "Ana")
        with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ben:
            hello(ben, "Ben")
            ana.receive_json()  # Ben joining is broadcast to Ana

            _ack, state = do(ana, "sit", seat=0, buy_in=200)
            ben.receive_json()
            assert state["state"]["hand"] is None

            _ack, state = do(ben, "sit", seat=1, buy_in=200)
            ana.receive_json()

            assert state["state"]["hand"] is not None
            mine = next(s for s in state["state"]["seats"] if s["player"]["you"])
            assert len(mine["cards"]) == 2


def test_the_player_to_act_is_told_what_they_may_do(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ana:
        hello(ana, "Ana")
        with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ben:
            hello(ben, "Ben")
            ana.receive_json()
            do(ana, "sit", seat=0, buy_in=200)
            ben.receive_json()
            _ack, state = do(ben, "sit", seat=1, buy_in=200)
            ana_state = ana.receive_json()

            for view in (state["state"], ana_state["state"]):
                legal = view["you"]["legal"]["actions"]
                if legal:
                    assert "fold" in legal
                    break
            else:
                raise AssertionError("nobody was given any legal action")


def test_an_illegal_action_comes_back_with_a_reason(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws, "Ana")
        ack, _ = do(ws, "act", action="fold")
        assert ack["t"] == "error"
        assert "no hand" in ack["message"].lower()


def test_an_action_replayed_after_a_reconnect_is_ignored(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        welcome, _ = hello(ws, "Ana")
        ws.send_json(
            {"t": "action", "aid": "sit-once", "kind": "sit", "data": {"seat": 3}}
        )
        assert ws.receive_json()["duplicate"] is False
        ws.receive_json()

    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws, "Ana", token=welcome["token"])
        # The client never saw the first ack, so it sends it again.
        ws.send_json(
            {"t": "action", "aid": "sit-once", "kind": "sit", "data": {"seat": 3}}
        )
        ack = ws.receive_json()
        assert ack["duplicate"] is True
        state = ws.receive_json()
        seats = [s["seat"] for s in state["state"]["seats"]]
        assert seats == [3], "sat down exactly once"


def test_chat_reaches_the_table(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ana:
        hello(ana, "Ana")
        _ack, state = do(ana, "say", text="nice hand")
        assert state["state"]["messages"][-1]["text"] == "nice hand"


# ------------------------------------------------------------- plumbing


def test_the_heartbeat_is_answered(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws)
        ws.send_json({"t": "ping", "at": 1234})
        assert ws.receive_json() == {"t": "pong", "at": 1234}


def test_acting_before_hello_is_refused(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        ws.send_json({"t": "action", "aid": "x", "kind": "say", "data": {}})
        assert ws.receive_json()["code"] == "not_joined"


def test_an_old_client_is_told_to_reload(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        ws.send_json({"t": "hello", "protocol": 0, "nick": "Ana"})
        assert ws.receive_json()["code"] == "protocol_mismatch"


def test_resync_returns_the_whole_state(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws)
        ws.send_json({"t": "resync"})
        assert ws.receive_json()["t"] == "state"


def test_rubbish_is_rejected_without_dropping_the_socket(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws)
        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "bad_json"
        ws.send_json({"t": "ping"})
        assert ws.receive_json()["t"] == "pong"


def test_a_flood_is_rate_limited(client):
    table_id = make_table(client)
    with client.websocket_connect(f"/ws?table={table_id}", headers=ORIGIN) as ws:
        hello(ws)
        replies = []
        for n in range(80):
            ws.send_json({"t": "ping", "at": n})
            replies.append(ws.receive_json())
        assert any(m.get("code") == "rate_limited" for m in replies)
