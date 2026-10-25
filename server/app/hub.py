"""Live connections, grouped by table.

The only thing kept in memory is the set of open sockets. Every byte that
matters is in Postgres, so a restart costs connections, never a hand.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .config import settings
from .ratelimit import TokenBucket

log = logging.getLogger("hub")


@dataclass(eq=False)  # identity, so a connection can live in a set
class Connection:
    ws: WebSocket
    table_id: str
    player_id: str | None = None
    bucket: TokenBucket = field(
        default_factory=lambda: TokenBucket(settings.rate_burst, settings.rate_per_second)
    )

    async def send(self, message: dict[str, Any]) -> bool:
        try:
            await self.ws.send_json(message)
            return True
        except Exception:  # noqa: BLE001 - a dead socket is not an error here
            return False


class Hub:
    def __init__(self) -> None:
        self._by_table: dict[str, set[Connection]] = {}
        self._lock = asyncio.Lock()

    async def add(self, conn: Connection) -> None:
        async with self._lock:
            self._by_table.setdefault(conn.table_id, set()).add(conn)

    async def remove(self, conn: Connection) -> None:
        async with self._lock:
            peers = self._by_table.get(conn.table_id)
            if peers is not None:
                peers.discard(conn)
                if not peers:
                    self._by_table.pop(conn.table_id, None)

    async def connections(self, table_id: str) -> list[Connection]:
        async with self._lock:
            return list(self._by_table.get(table_id, ()))

    async def others_as_player(
        self, table_id: str, player_id: str, keep: Connection
    ) -> list[Connection]:
        """Other sockets claiming to be the same player (one device wins)."""
        return [
            c
            for c in await self.connections(table_id)
            if c.player_id == player_id and c is not keep
        ]

    async def broadcast(self, table_id: str, build: Any) -> None:
        """Send each connection the message `build(connection)` returns.

        Each viewer gets their own message, because each viewer is allowed to
        see different things — and for the same reason each one is built
        and sent on its own. If building one viewer's message fails, that
        viewer misses this update and the rest of the table carries on;
        letting one bad snapshot deny everybody else is far worse, and
        leaves players staring at a screen that never arrives.
        """
        for conn in await self.connections(table_id):
            try:
                message = build(conn)
            except Exception:  # noqa: BLE001 - one viewer, not the table
                log.exception(
                    "could not build the view for %s at table %s",
                    conn.player_id,
                    table_id,
                )
                continue
            if message is not None:
                await conn.send(message)
