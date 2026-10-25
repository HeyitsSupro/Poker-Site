"""The DSN handling and the retry policy that carry us over Neon's suspends."""

from __future__ import annotations

import asyncio
import ssl

import asyncpg
import pytest

from app.db import Database, normalise_dsn


def test_neon_style_dsn_is_accepted():
    dsn, ssl_arg = normalise_dsn(
        "postgresql://u:p@ep-cool-pooler.eu-central-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    # channel_binding would be rejected by asyncpg, so it is dropped.
    assert "channel_binding" not in dsn
    assert "sslmode" not in dsn
    assert dsn.startswith("postgresql://u:p@ep-cool-pooler.")
    assert isinstance(ssl_arg, ssl.SSLContext)


def test_tls_is_the_default_when_no_sslmode_is_given():
    _, ssl_arg = normalise_dsn("postgresql://u:p@host/db")
    assert ssl_arg is True


def test_sslmode_disable_is_honoured():
    _, ssl_arg = normalise_dsn("postgresql://u:p@localhost/db?sslmode=disable")
    assert ssl_arg is False


def test_unknown_sslmode_is_refused():
    with pytest.raises(ValueError):
        normalise_dsn("postgresql://u:p@host/db?sslmode=sort-of")


class _FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


def _stub_pool(db: Database, monkeypatch) -> None:
    """Let the real retry loop run without a real database behind it."""

    async def fake_connect():
        db._pool = _FakePool()

    async def fake_reset():
        db._pool = None

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "_reset_pool", fake_reset)
    monkeypatch.setattr(asyncio, "sleep", no_wait)


async def test_run_retries_a_dropped_connection_then_succeeds(monkeypatch):
    """The first calls after a suspend fail; a later one must go through."""
    db = Database("postgresql://u:p@host/db?sslmode=disable")
    _stub_pool(db, monkeypatch)
    calls = {"n": 0}

    async def flaky(_conn):
        calls["n"] += 1
        if calls["n"] < 3:
            raise asyncpg.exceptions.ConnectionDoesNotExistError("neon is asleep")
        return "woke up"

    assert await db.run(flaky) == "woke up"
    assert calls["n"] == 3


async def test_a_query_error_is_not_retried(monkeypatch):
    """Retrying is for lost connections, not for broken SQL."""
    db = Database("postgresql://u:p@host/db?sslmode=disable")
    _stub_pool(db, monkeypatch)
    calls = {"n": 0}

    async def broken(_conn):
        calls["n"] += 1
        raise asyncpg.exceptions.UndefinedTableError("relation does not exist")

    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await db.run(broken)
    assert calls["n"] == 1


async def test_run_gives_up_after_the_attempt_limit(monkeypatch):
    db = Database("postgresql://u:p@host/db?sslmode=disable", attempts=4)
    _stub_pool(db, monkeypatch)
    calls = {"n": 0}

    async def always_down(_conn):
        calls["n"] += 1
        raise asyncpg.exceptions.CannotConnectNowError("starting up")

    with pytest.raises(asyncpg.exceptions.CannotConnectNowError):
        await db.run(always_down)
    assert calls["n"] == 4


async def test_an_unreachable_database_eventually_raises():
    db = Database("postgresql://u:p@127.0.0.1:1/db?sslmode=disable", attempts=2)
    with pytest.raises(OSError):
        await db.fetchval("SELECT 1")
    assert await db.ping(timeout=1.0) is False
