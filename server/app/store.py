"""Persistence for tables, events and hand history.

The rule from the build instructions (section 4.2) is: save after every
action, never on a timer. `apply_action` is the only way state changes. It
takes a *pure* mutate function, runs it inside one transaction together with
the event rows it produced, and commits them as a unit. Either the action
happened and was logged, or neither.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from .config import STATE_FORMAT_VERSION
from .db import Database
from .migrations import migrate

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

State = dict[str, Any]


class TableNotFound(Exception):
    pass


@dataclass(frozen=True)
class Event:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str | None = None


@dataclass(frozen=True)
class ApplyResult:
    state: State
    seq: int
    events: list[Event]
    duplicate: bool


# A mutate function: takes the current state, returns the next state and the
# events to log. It must not touch the network or the database, and must be
# safe to call with the same input twice.
Mutate = Callable[[State], tuple[State, list[Event]]]


def new_table_id() -> str:
    """A long random id: the table link is the only thing gating entry."""
    return secrets.token_urlsafe(16)


class TableStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_schema(self) -> None:
        sql = SCHEMA_PATH.read_text()
        await self.db.run(lambda c: c.execute(sql))

    # ------------------------------------------------------------- tables

    async def create_table(self, state: State, table_id: str | None = None) -> str:
        table_id = table_id or new_table_id()

        async def tx(conn: asyncpg.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO tables (table_id, format_version, seq, state)
                VALUES ($1, $2, 0, $3::JSONB)
                ON CONFLICT (table_id) DO NOTHING
                """,
                table_id,
                STATE_FORMAT_VERSION,
                json.dumps(state),
            )
            await conn.execute(
                """
                INSERT INTO events (table_id, seq, idx, kind, payload)
                VALUES ($1, 0, 0, 'table_created', '{}'::JSONB)
                ON CONFLICT DO NOTHING
                """,
                table_id,
            )

        await self.db.transaction(tx)
        return table_id

    async def load(self, table_id: str) -> tuple[State, int]:
        row = await self.db.fetchrow(
            "SELECT format_version, seq, state FROM tables WHERE table_id = $1",
            table_id,
        )
        if row is None:
            raise TableNotFound(table_id)
        state = migrate(json.loads(row["state"]), row["format_version"])
        return state, row["seq"]

    async def list_open_tables(self) -> list[str]:
        rows = await self.db.fetch(
            "SELECT table_id FROM tables WHERE NOT closed ORDER BY updated_at DESC"
        )
        return [r["table_id"] for r in rows]

    async def tables_with_deadlines(
        self, within_hours: int = 24
    ) -> list[tuple[str, float]]:
        """Open tables that have a countdown running, and when it is due.

        One query, rather than loading every open table to look. A server
        that has been running for months can have a great many finished-but-
        open tables, and reading them all back at boot would make every
        deploy and every wake-up slow — on a free instance, slow enough to
        matter.

        Tables untouched for a long time are left out: nothing is waiting on
        a thirty-second clock from last week, and if somebody does come
        back, the first action resets the clock anyway.
        """
        rows = await self.db.fetch(
            """
            SELECT table_id, (state -> 'deadline' ->> 'at')::DOUBLE PRECISION AS at
            FROM tables
            WHERE NOT closed
              AND state -> 'deadline' IS NOT NULL
              AND state -> 'deadline' <> 'null'::JSONB
              AND updated_at > now() - make_interval(hours => $1)
            """,
            within_hours,
        )
        return [(r["table_id"], r["at"]) for r in rows if r["at"] is not None]

    async def close_table(self, table_id: str) -> None:
        await self.db.execute(
            "UPDATE tables SET closed = TRUE, updated_at = now() WHERE table_id = $1",
            table_id,
        )

    # ------------------------------------------------------------ actions

    async def apply_action(
        self,
        table_id: str,
        action_id: str,
        mutate: Mutate,
        actor: str | None = None,
    ) -> ApplyResult:
        """Apply one action atomically, or recognise it as a replay.

        `FOR UPDATE` serialises concurrent actions on a single table, which
        is exactly the invariant a poker table needs: one action at a time,
        applied to the state everyone has just seen.
        """

        async def tx(conn: asyncpg.Connection) -> ApplyResult:
            row = await conn.fetchrow(
                """
                SELECT format_version, seq, state, closed
                FROM tables WHERE table_id = $1
                FOR UPDATE
                """,
                table_id,
            )
            if row is None:
                raise TableNotFound(table_id)

            state = migrate(json.loads(row["state"]), row["format_version"])

            already = await conn.fetchval(
                "SELECT seq FROM applied_actions WHERE table_id = $1 AND action_id = $2",
                table_id,
                action_id,
            )
            if already is not None:
                # Seen before: hand back the committed state unchanged.
                return ApplyResult(
                    state=state, seq=row["seq"], events=[], duplicate=True
                )

            next_state, events = mutate(state)
            seq = row["seq"] + 1

            await conn.execute(
                """
                UPDATE tables
                   SET state = $1::JSONB,
                       seq = $2,
                       format_version = $3,
                       updated_at = now()
                 WHERE table_id = $4
                """,
                json.dumps(next_state),
                seq,
                STATE_FORMAT_VERSION,
                table_id,
            )
            for idx, event in enumerate(events):
                await conn.execute(
                    """
                    INSERT INTO events (table_id, seq, idx, kind, actor, payload)
                    VALUES ($1, $2, $3, $4, $5, $6::JSONB)
                    """,
                    table_id,
                    seq,
                    idx,
                    event.kind,
                    event.actor if event.actor is not None else actor,
                    json.dumps(event.payload),
                )
            await conn.execute(
                """
                INSERT INTO applied_actions (table_id, action_id, seq)
                VALUES ($1, $2, $3)
                """,
                table_id,
                action_id,
                seq,
            )
            return ApplyResult(
                state=next_state, seq=seq, events=events, duplicate=False
            )

        return await self.db.transaction(tx)

    # ------------------------------------------------- log & hand history

    async def events_since(self, table_id: str, seq: int) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            """
            SELECT seq, idx, at, kind, actor, payload
            FROM events
            WHERE table_id = $1 AND seq > $2
            ORDER BY seq, idx
            """,
            table_id,
            seq,
        )
        return [
            {
                "seq": r["seq"],
                "idx": r["idx"],
                "at": r["at"].isoformat(),
                "kind": r["kind"],
                "actor": r["actor"],
                "payload": json.loads(r["payload"]),
            }
            for r in rows
        ]

    async def append_hand(
        self, table_id: str, hand_no: int, data: dict[str, Any]
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO hands (table_id, hand_no, format_version, data)
            VALUES ($1, $2, $3, $4::JSONB)
            ON CONFLICT (table_id, hand_no) DO NOTHING
            """,
            table_id,
            hand_no,
            STATE_FORMAT_VERSION,
            json.dumps(data),
        )

    async def list_hands(
        self, table_id: str, before: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recent hand numbers, newest first, for the history list.

        Only the hand number and when it ended: the hands themselves carry
        hole cards, so they are fetched one at a time and redacted on the
        way out (see `table.replay_for`).
        """
        limit = max(1, min(int(limit), 100))
        if before is None:
            rows = await self.db.fetch(
                """
                SELECT hand_no, ended_at FROM hands
                WHERE table_id = $1
                ORDER BY hand_no DESC LIMIT $2
                """,
                table_id,
                limit,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT hand_no, ended_at FROM hands
                WHERE table_id = $1 AND hand_no < $2
                ORDER BY hand_no DESC LIMIT $3
                """,
                table_id,
                int(before),
                limit,
            )
        return [
            {"hand_no": r["hand_no"], "ended_at": r["ended_at"].isoformat()}
            for r in rows
        ]

    async def get_hand(self, table_id: str, hand_no: int) -> dict[str, Any] | None:
        row = await self.db.fetchrow(
            """
            SELECT format_version, data FROM hands
            WHERE table_id = $1 AND hand_no = $2
            """,
            table_id,
            int(hand_no),
        )
        if row is None:
            return None
        return migrate(json.loads(row["data"]), row["format_version"])

    async def prune_dedupe(self, hours: int) -> int:
        deleted = await self.db.fetchval(
            """
            WITH gone AS (
                DELETE FROM applied_actions
                WHERE at < now() - make_interval(hours => $1)
                RETURNING 1
            )
            SELECT count(*) FROM gone
            """,
            hours,
        )
        return int(deleted or 0)
