"""FastAPI app: health, table creation, and the live WebSocket.

Phase 1. The toy table of phase 0 is gone; this serves real Hold'em on top
of the engine, with the persistence, reconnection and leak guarantees that
phase 0 established unchanged:

  * a health endpoint that answers while the database is still waking
  * every action saved in one transaction before anyone is told it happened
  * action ids, so a reconnect that re-sends an action changes nothing
  * per-viewer snapshots, so hole cards never reach the wrong socket
  * Origin checks and rate limiting on the socket
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import clock, host as host_controls, identity, table as table_model
from . import ticker as ticker_module
from .config import PROTOCOL_VERSION, STATE_FORMAT_VERSION, settings
from .db import Database
from .hub import Connection, Hub
from .migrations import UnmigratableState
from .protocol import (
    MAX_CHAT_LENGTH,
    MAX_NICK_LENGTH,
    ProtocolError,
    check_protocol,
    clean_text,
    error,
    parse,
)
from .store import TableNotFound, TableStore

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("app")

STARTED_AT = time.time()

# Starlette has grown a second disconnect exception over the years, and which
# one arrives depends on when the socket went away. Both just mean "gone".
_disconnects: list[type[BaseException]] = [WebSocketDisconnect]
try:  # pragma: no cover - depends on the installed starlette
    from starlette.websockets import WebSocketDisconnected

    _disconnects.append(WebSocketDisconnected)
except ImportError:  # pragma: no cover
    pass
DISCONNECTED = tuple(_disconnects)

hub = Hub()
db: Database | None = None
store: TableStore | None = None
# Wakes a table when one of its countdowns falls due. `fire_tick` is defined
# below; the late binding keeps the module import order simple.
ticker = ticker_module.Ticker(lambda table_id: fire_tick(table_id))

# The health endpoint must answer immediately even when Neon is suspended, so
# the database probe is cached briefly rather than run on every poll.
_db_probe: tuple[float, bool] = (0.0, False)
_DB_PROBE_TTL = 3.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, store
    if not settings.has_database:
        log.warning("DATABASE_URL is not set; starting without persistence")
    else:
        db = Database(settings.database_url)
        store = TableStore(db)
        try:
            await store.ensure_schema()
            open_tables = await store.list_open_tables()
            log.info("schema ready; %d open table(s) resumed", len(open_tables))
            await store.prune_dedupe(settings.dedupe_retention_hours)
            # Every pending countdown is rebuilt, so a restart mid-hand
            # does not leave a player's clock stopped. One query finds
            # them: reading back every open table here would make each
            # deploy and each wake-up slower as the months go by.
            pending = await store.tables_with_deadlines()
            for table_id, at in pending:
                ticker.schedule_at(table_id, at)
            if pending:
                log.info("restarted %d pending timer(s)", len(pending))
        except Exception as exc:  # noqa: BLE001
            # Never crash-loop on a sleeping database: the health endpoint
            # reports it and the next request retries.
            log.error("database not ready at startup: %s", exc)
    try:
        yield
    finally:
        ticker.cancel_all()
        if db is not None:
            await db.close()


app = FastAPI(title="Poker table server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)


def require_store() -> TableStore:
    if store is None:
        raise HTTPException(503, "The server has no database configured.")
    return store


async def db_is_up() -> bool:
    global _db_probe
    if db is None:
        return False
    checked_at, result = _db_probe
    now = time.monotonic()
    if now - checked_at < _DB_PROBE_TTL:
        return result
    result = await db.ping(timeout=3.0)
    _db_probe = (now, result)
    return result


# ------------------------------------------------------------------- HTTP


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Wakes the service and reports whether it can really serve a table.

    Always 200 so Render's health check passes while Neon is still resuming;
    the client watches `ready`, not the status code.
    """
    database = await db_is_up()
    return JSONResponse(
        {
            "ok": True,
            "ready": database,
            "database": "up" if database else "waking",
            "uptime_seconds": round(time.time() - STARTED_AT, 1),
            "protocol": PROTOCOL_VERSION,
            "state_version": STATE_FORMAT_VERSION,
            "heartbeat_seconds": settings.heartbeat_seconds,
        }
    )


@app.post("/api/tables")
async def create_table(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    s = require_store()
    payload = payload or {}
    name = clean_text(payload.get("name"), 40, "Home game")

    # The host can set the game up as they create it. Every value goes
    # through the same checks the host panel uses, so a bad one is refused
    # here rather than becoming a table nobody can play on.
    wanted = payload.get("settings")
    chosen: dict[str, Any] = {}
    if isinstance(wanted, dict):
        try:
            chosen = {
                key: host_controls._clean_setting(key, value)
                for key, value in wanted.items()
                if key in host_controls.SETTING_RULES
            }
        except table_model.Rejected as exc:
            raise HTTPException(400, str(exc)) from None

    table_id = await s.create_table(table_model.new_state(name, chosen))
    log.info("created table %s", table_id)
    return {"table_id": table_id}


@app.get("/api/tables/{table_id}")
async def table_exists(table_id: str) -> dict[str, Any]:
    s = require_store()
    try:
        state, seq = await s.load(table_id)
    except TableNotFound:
        raise HTTPException(404, "No table with that link.") from None
    return {
        "table_id": table_id,
        "name": state.get("name"),
        "seq": seq,
        "players": len(state.get("players", {})),
    }


# -------------------------------------------------------------- WebSocket


def origin_allowed(origin: str | None) -> bool:
    if origin is None:
        return settings.allow_missing_origin
    if not settings.allowed_origins:
        # No allow-list configured: local development only.
        return True
    return origin.rstrip("/") in settings.allowed_origins


async def broadcast_state(table_id: str, state: dict) -> None:
    await hub.broadcast(
        table_id,
        lambda c: {
            "t": "state",
            "version": STATE_FORMAT_VERSION,
            "state": table_model.view_for(state, c.player_id),
        },
    )


async def send_state(conn: Connection, state: dict) -> None:
    await conn.send(
        {
            "t": "state",
            "version": STATE_FORMAT_VERSION,
            "state": table_model.view_for(state, conn.player_id),
        }
    )


async def apply(
    s: TableStore, conn: Connection, aid: str, mutate, *, actor: str | None = None
):
    """Run one mutate, file the hand history, and tell everyone."""
    try:
        result = await s.apply_action(
            conn.table_id, aid, mutate, actor=actor or conn.player_id
        )
    except table_model.Rejected as exc:
        raise ProtocolError("rejected", str(exc), aid) from None
    except ValueError as exc:
        raise ProtocolError("rejected", str(exc), aid) from None

    # A finished hand is written to the append-only history. It carries hole
    # cards and the remainder of the deck, so it is filed here and never
    # broadcast.
    for event in result.events:
        if event.kind == "hand_history":
            await s.append_hand(
                conn.table_id, event.payload["hand_no"], event.payload["hand"]
            )

    await conn.send({"t": "ack", "aid": aid, "duplicate": result.duplicate})
    if result.duplicate:
        await send_state(conn, result.state)
    else:
        await broadcast_state(conn.table_id, result.state)
    # Whatever just happened may have started or cleared a countdown.
    ticker.schedule(conn.table_id, result.state)
    return result


async def fire_tick(table_id: str) -> None:
    """Apply a deadline that has fallen due, and tell everyone.

    Submitted like any other action, so it is saved in one transaction with
    its effects and a repeat of the same deadline is ignored as a duplicate.
    """
    if store is None:
        return
    try:
        state, _seq = await store.load(table_id)
    except TableNotFound:
        return

    deadline = clock.get(state)
    if deadline is None:
        return

    try:
        result = await store.apply_action(
            table_id,
            ticker_module.tick_action_id(table_id, deadline),
            table_model.tick(),
            actor=None,
        )
    except Exception as exc:  # noqa: BLE001 - never let a timer kill the server
        log.exception("tick failed for %s: %s", table_id, exc)
        return

    for event in result.events:
        if event.kind == "hand_history":
            await store.append_hand(
                table_id, event.payload["hand_no"], event.payload["hand"]
            )

    if not result.duplicate:
        await broadcast_state(table_id, result.state)
    # A tick often sets the next one (the clock passes to the next player).
    ticker.schedule(table_id, result.state)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not origin_allowed(ws.headers.get("origin")):
        # 1008 = policy violation. Refuse before accepting the socket.
        await ws.close(code=1008)
        return

    table_id = ws.query_params.get("table", "")
    if not table_id:
        await ws.close(code=1008)
        return

    await ws.accept()
    conn = Connection(ws=ws, table_id=table_id)
    s = store
    if s is None:
        await conn.send(error("no_database", "The server has no database configured."))
        await ws.close(code=1011)
        return

    await hub.add(conn)
    try:
        await _serve(conn, s)
    except DISCONNECTED:
        # A closed socket is the normal end of a connection, not a fault.
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("socket failed: %s", exc)
    finally:
        await hub.remove(conn)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def _serve(conn: Connection, s: TableStore) -> None:
    hello_done = False

    while True:
        try:
            raw = await asyncio.wait_for(
                conn.ws.receive_text(), timeout=settings.idle_timeout_seconds
            )
        except asyncio.TimeoutError:
            await conn.send(error("idle", "Connection closed after being idle."))
            return

        if not conn.bucket.take():
            await conn.send(error("rate_limited", "Slow down a moment."))
            continue

        try:
            msg = parse(raw, settings.max_message_bytes)
            kind = msg["t"]

            if kind == "ping":
                await conn.send({"t": "pong", "at": msg.get("at")})
                continue

            if kind == "hello":
                check_protocol(msg)
                await _handle_hello(conn, s, msg)
                hello_done = True
                continue

            if not hello_done:
                raise ProtocolError("not_joined", "Say hello first.")

            if kind == "history":
                await _send_history(conn, s, msg)
                continue

            if kind == "resync":
                state, _seq = await s.load(conn.table_id)
                await send_state(conn, state)
                continue

            if kind == "action":
                await _handle_action(conn, s, msg)
                continue

            raise ProtocolError("unknown_type", f"Unknown message type {kind!r}.")

        except ProtocolError as exc:
            await conn.send(error(exc.code, exc.message, exc.aid))
        except TableNotFound:
            await conn.send(error("no_table", "No table with that link."))
            return
        except UnmigratableState as exc:
            log.error("cannot load table %s: %s", conn.table_id, exc)
            await conn.send(
                error(
                    "stale_state",
                    "This table was saved by a different version of the server.",
                )
            )
            return


async def _handle_hello(conn: Connection, s: TableStore, msg: dict) -> None:
    """Work out who this is, in the order section 4.5 lays down.

    A saved token first, then a rejoin link, then "I'm an existing player"
    which the host has to approve, and only then a new player. A nickname on
    its own never identifies anybody.
    """
    state, _seq = await s.load(conn.table_id)

    token = msg.get("token") if isinstance(msg.get("token"), str) else None
    rejoin = msg.get("rejoin") if isinstance(msg.get("rejoin"), str) else None
    nick = clean_text(msg.get("nick"), MAX_NICK_LENGTH, "Player")

    player_id = identity.find_by_token(state, token)
    issued_token: str | None = None
    issued_rejoin: str | None = None

    if player_id is None and rejoin:
        # A rejoin link from another device: adopt it, and this device's new
        # token replaces whatever the old one had.
        claimed = identity.find_by_rejoin(state, rejoin)
        if claimed is None:
            raise ProtocolError(
                "bad_rejoin", "That rejoin link is not valid for this table."
            )
        issued_token = identity.new_secret()
        result = await s.apply_action(
            conn.table_id,
            f"adopt:{identity.hash_secret(issued_token)[:24]}",
            table_model.adopt_token(claimed, issued_token),
            actor=claimed,
        )
        state, player_id = result.state, claimed

    if player_id is None and msg.get("spectate") is True:
        conn.player_id = None
        await conn.send(
            {
                "t": "welcome",
                "protocol": PROTOCOL_VERSION,
                "table": conn.table_id,
                "player_id": None,
                "token": None,
                "spectating": True,
                "heartbeat": settings.heartbeat_seconds,
            }
        )
        await send_state(conn, state)
        return

    if player_id is None and msg.get("claim"):
        # "I'm an existing player" on a new device: the host decides.
        wanted = str(msg["claim"])
        if wanted not in state.get("players", {}):
            raise ProtocolError("no_such_player", "No player by that name here.")
        pending_token = identity.new_secret()
        claim_id = secrets.token_urlsafe(8)
        await s.apply_action(
            conn.table_id,
            f"claim:{claim_id}",
            table_model.request_claim(claim_id, wanted, pending_token, nick),
            actor=wanted,
        )
        state, _ = await s.load(conn.table_id)
        await conn.send(
            {
                "t": "claim_pending",
                "claim": claim_id,
                "token": pending_token,
                "message": "Waiting for the host to confirm it is you.",
            }
        )
        await broadcast_state(conn.table_id, state)
        return

    if player_id is None:
        # Brand new player. Both secrets are made here, never accepted from
        # the client, and only ever sent back to their owner.
        issued_token = identity.new_secret()
        issued_rejoin = identity.new_secret()
        host_code = identity.new_secret()
        result = await s.apply_action(
            conn.table_id,
            f"join:{identity.hash_secret(issued_token)[:24]}",
            table_model.join(nick, issued_token, issued_rejoin, host_code),
        )
        state = result.state
        player_id = next(
            (
                event.payload["player_id"]
                for event in result.events
                if event.kind == "player_joined"
            ),
            None,
        ) or identity.find_by_token(state, issued_token)

    conn.player_id = player_id

    # One active device per player: the newcomer takes over.
    for other in await hub.others_as_player(conn.table_id, player_id, conn):
        await other.send({"t": "replaced"})

    welcome: dict[str, Any] = {
        "t": "welcome",
        "protocol": PROTOCOL_VERSION,
        "table": conn.table_id,
        "player_id": player_id,
        "heartbeat": settings.heartbeat_seconds,
    }
    if issued_token:
        welcome["token"] = issued_token
    if issued_rejoin:
        welcome["rejoin"] = issued_rejoin
        welcome["host_code"] = None
    await conn.send(welcome)
    await broadcast_state(conn.table_id, state)


# Table actions a seated player may take, mapped to the mutate that does it.
def _mutate_for(conn: Connection, kind: str, data: dict, state: dict, aid: str):
    pid = conn.player_id
    if kind == "sit":
        settings = state.get("settings", {})
        resume = bool(data.get("resume"))
        # When the host is vetting buy-ins, a new buy-in joins a queue
        # instead of a seat. Resuming your own chips is not a buy-in, so it
        # never waits.
        if (
            not resume
            and not settings.get("auto_approve_buy_ins", True)
            and not identity.is_host(state, pid)
        ):
            return host_controls.request_buy_in(
                pid,
                int(data.get("seat", 0)),
                int(data.get("buy_in") or settings.get("buy_in", 0)),
                aid,
            )
        return table_model.sit(
            pid,
            int(data.get("seat", 0)),
            data.get("buy_in"),
            resume,
        )
    if kind == "stand":
        return table_model.stand_up(pid)
    if kind == "away":
        return table_model.set_away(pid, bool(data.get("away", True)))
    if kind == "cash_out":
        return table_model.cash_out(pid)
    if kind == "resume_cash_out":
        return table_model.resume_cash_out(pid, int(data.get("seat", 0)))
    if kind == "act":
        return table_model.act(
            pid,
            str(data.get("action", "")),
            int(data["amount"]) if data.get("amount") is not None else None,
        )
    if kind == "say":
        return table_model.say(pid, clean_text(data.get("text"), MAX_CHAT_LENGTH))
    if kind == "choose_runs":
        return table_model.choose_runs(pid, int(data.get("count", 1)))
    if kind == "show":
        # Which of your two cards to turn over: both, one, or none.
        cards = data.get("cards")
        chosen = (
            [int(i) for i in cards][:2] if isinstance(cards, list) else [0, 1]
        )
        return table_model.show_or_muck(pid, chosen)
    if kind == "muck":
        return table_model.show_or_muck(pid, [])
    if kind == "auto_muck":
        return table_model.set_auto_muck(pid, bool(data.get("on", True)))
    if kind == "rabbit":
        return table_model.reveal(pid, int(data.get("index", 0)))
    if kind == "react":
        return table_model.react(pid, str(data.get("emoji", "")))
    if kind == "waitlist":
        seat = data.get("seat")
        return table_model.join_waitlist(
            pid, int(seat) if seat is not None else None
        )
    if kind == "leave_waitlist":
        return table_model.leave_waitlist(pid)
    if kind == "note":
        return table_model.set_note(
            pid, str(data.get("about", "")), str(data.get("text", ""))
        )
    if kind == "pause":
        return table_model.pause(pid, bool(data.get("paused", True)))
    if kind == "resolve_claim":
        return table_model.resolve_claim(
            str(data.get("claim", "")), bool(data.get("approve")), pid
        )
    if kind == "straddle":
        return host_controls.post_straddle(pid, int(data.get("amount", 0)))

    # --- host and co-host only; each of these checks for itself ---
    if kind == "settings":
        changes = data.get("changes")
        return host_controls.change_settings(
            pid, changes if isinstance(changes, dict) else {}
        )
    if kind == "save_preset":
        return host_controls.save_preset(pid, str(data.get("name", "")))
    if kind == "load_preset":
        return host_controls.load_preset(pid, str(data.get("name", "")))
    if kind == "delete_preset":
        return host_controls.delete_preset(pid, str(data.get("name", "")))
    if kind == "edit_stack":
        return host_controls.edit_stack(
            pid,
            str(data.get("player_id", "")),
            int(data.get("amount", 0)),
            clean_text(data.get("reason"), 120) or None,
        )
    if kind == "co_host":
        return host_controls.set_co_host(
            pid, str(data.get("player_id", "")), bool(data.get("on"))
        )
    if kind == "claim_host":
        return host_controls.claim_host(pid, str(data.get("code", "")))
    if kind == "remove_player":
        return host_controls.remove_player(
            pid, str(data.get("player_id", "")), bool(data.get("revoke"))
        )
    if kind == "set_away":
        return host_controls.set_player_away(
            pid, str(data.get("player_id", "")), bool(data.get("away"))
        )
    if kind == "resolve_buy_in":
        return host_controls.resolve_buy_in(
            pid, str(data.get("request", "")), bool(data.get("approve"))
        )
    if kind == "end_game":
        return host_controls.end_game(pid)
    return None


async def _send_history(conn: Connection, s: TableStore, msg: dict) -> None:
    """Answer a request for the hand history.

    A read, not an action: nothing changes, so it carries no action id and
    is not saved. Everything it returns goes through `replay_for`, which
    decides what this viewer is allowed to know about a finished hand.
    """
    hand_no = msg.get("hand_no")
    if hand_no is None:
        before = msg.get("before")
        hands = await s.list_hands(
            conn.table_id,
            before=int(before) if before is not None else None,
            limit=int(msg.get("limit", 20)),
        )
        await conn.send({"t": "history", "hands": hands})
        return

    stored = await s.get_hand(conn.table_id, int(hand_no))
    if stored is None:
        await conn.send({"t": "replay", "hand_no": int(hand_no), "hand": None})
        return

    state, _seq = await s.load(conn.table_id)
    await conn.send(
        {
            "t": "replay",
            "hand_no": int(hand_no),
            "hand": table_model.replay_for(stored, state, conn.player_id),
        }
    )


async def _handle_action(conn: Connection, s: TableStore, msg: dict) -> None:
    aid = msg.get("aid")
    if not isinstance(aid, str) or not aid:
        raise ProtocolError("no_action_id", "That action had no id.")
    if conn.player_id is None:
        raise ProtocolError("spectator", "Spectators cannot act.", aid)

    kind = msg.get("kind")
    data = msg.get("data") if isinstance(msg.get("data"), dict) else {}

    if kind == "new_rejoin_link":
        # Handed over once, here, and never again; it is not in any snapshot.
        rejoin = identity.new_secret()
        await apply(
            s, conn, aid, table_model.regenerate_rejoin(conn.player_id, rejoin)
        )
        await conn.send({"t": "rejoin_link", "rejoin": rejoin})
        return

    # A couple of actions branch on the table's own settings, so the state
    # is read before choosing which mutate to run. The mutate itself still
    # re-checks everything inside the transaction.
    state, _seq = await s.load(conn.table_id)
    mutate = _mutate_for(conn, str(kind), data, state, aid)
    if mutate is None:
        raise ProtocolError("unknown_action", f"Unknown action {kind!r}.", aid)

    await apply(s, conn, aid, mutate)
