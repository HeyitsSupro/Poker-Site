"""Postgres access that survives Neon's scale-to-zero.

Neon's free compute suspends after 5 idle minutes and resumes on the next
query. The first query after a suspend can fail outright (dead socket, DNS
still settling, "the database system is starting up") before the second one
succeeds, so every call here goes through `run()`, which retries a handful
of times with backoff.

Retrying is only safe because of how callers use it:

  * reads are naturally idempotent;
  * writes go through `transaction()`, and the one write path that matters
    (`TableStore.apply_action`) is guarded by a unique action id, so a
    transaction that secretly committed before the connection dropped is
    recognised as a duplicate on the retry instead of being applied twice.
"""

from __future__ import annotations

import asyncio
import logging
import random
import ssl as ssl_module
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

log = logging.getLogger("db")

T = TypeVar("T")

# Errors that mean "the connection went away", not "your query is wrong".
RETRYABLE = (
    asyncpg.exceptions.PostgresConnectionError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    ConnectionError,
    OSError,
    asyncio.TimeoutError,
)

# asyncpg understands these DSN query parameters. Neon hands out strings
# containing others (channel_binding, options for pooler routing); passing an
# unknown one through would be rejected, so we filter and translate instead.
_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_PASSTHROUGH_PARAMS = {"application_name"}


def normalise_dsn(dsn: str) -> tuple[str, Any]:
    """Split a Neon-style URL into (dsn asyncpg accepts, ssl argument)."""
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    sslmode = (query.pop("channel_binding", None), query.pop("sslmode", None))[1]
    kept = {k: v for k, v in query.items() if k in _PASSTHROUGH_PARAMS}
    clean = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )

    if sslmode is None:
        # Neon always wants TLS; default to it unless told otherwise.
        ssl_arg: Any = True
    elif sslmode not in _SSL_MODES:
        raise ValueError(f"unknown sslmode in DATABASE_URL: {sslmode!r}")
    elif sslmode == "disable":
        ssl_arg = False
    elif sslmode in {"allow", "prefer", "require"}:
        # `require` means encrypt, not "verify the certificate chain".
        ctx = ssl_module.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl_module.CERT_NONE
        ssl_arg = ctx
    else:
        ssl_arg = ssl_module.create_default_context()

    return clean, ssl_arg


class Database:
    """A thin, retrying wrapper around an asyncpg pool."""

    def __init__(
        self,
        dsn: str,
        *,
        max_size: int = 5,
        attempts: int = 5,
        command_timeout: float = 15.0,
    ) -> None:
        self._dsn, self._ssl = normalise_dsn(dsn)
        self._max_size = max_size
        self._attempts = attempts
        self._command_timeout = command_timeout
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- pool

    async def connect(self) -> None:
        async with self._lock:
            if self._pool is not None:
                return
            self._pool = await asyncpg.create_pool(
                self._dsn,
                ssl=self._ssl,
                # Keep no idle connections: Neon suspends anyway, and a free
                # project has few connection slots to spare.
                min_size=0,
                max_size=self._max_size,
                # Recycle before Neon's 5-minute suspend so we hand out fresh
                # sockets rather than discovering dead ones mid-hand.
                max_inactive_connection_lifetime=240.0,
                command_timeout=self._command_timeout,
                # Required when the connection string points at Neon's
                # pooled (-pooler) endpoint: PgBouncer in transaction mode
                # cannot serve asyncpg's prepared-statement cache.
                statement_cache_size=0,
            )

    async def close(self) -> None:
        async with self._lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None

    async def _reset_pool(self) -> None:
        """Throw the pool away so the next attempt dials Neon afresh."""
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await asyncio.wait_for(pool.close(), timeout=5.0)
            except Exception:  # noqa: BLE001 - best effort teardown
                pool.terminate()

    # --------------------------------------------------------------- retry

    async def run(self, fn: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
        """Run `fn` with a connection, retrying while Neon wakes up."""
        last: Exception | None = None
        for attempt in range(self._attempts):
            try:
                if self._pool is None:
                    await self.connect()
                assert self._pool is not None
                async with self._pool.acquire() as conn:
                    return await fn(conn)
            except RETRYABLE as exc:
                last = exc
                if attempt == self._attempts - 1:
                    break
                delay = min(0.3 * (2.4**attempt), 6.0)
                delay *= 0.75 + random.random() * 0.5  # jitter
                log.warning(
                    "database call failed (%s: %s); retry %d in %.1fs",
                    type(exc).__name__,
                    exc,
                    attempt + 1,
                    delay,
                )
                await self._reset_pool()
                await asyncio.sleep(delay)
        assert last is not None
        raise last

    async def transaction(
        self, fn: Callable[[asyncpg.Connection], Awaitable[T]]
    ) -> T:
        """Run `fn` inside a transaction, with the same retry policy.

        Callers must make `fn` safe to run twice (see the module docstring).
        """

        async def wrapped(conn: asyncpg.Connection) -> T:
            async with conn.transaction():
                return await fn(conn)

        return await self.run(wrapped)

    # ------------------------------------------------------------ shortcuts

    async def execute(self, query: str, *args: Any) -> str:
        return await self.run(lambda c: c.execute(query, *args))

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        return await self.run(lambda c: c.fetch(query, *args))

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        return await self.run(lambda c: c.fetchrow(query, *args))

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self.run(lambda c: c.fetchval(query, *args))

    async def ping(self, timeout: float = 3.0) -> bool:
        """Cheap liveness probe. Never raises."""
        try:
            await asyncio.wait_for(self.fetchval("SELECT 1"), timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 - the caller only wants up/down
            return False
