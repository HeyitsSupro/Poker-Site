"""Test configuration.

The store tests run against a real PostgreSQL, because the things worth
proving here — transactions, `FOR UPDATE`, unique-key dedupe, JSONB round
trips — are exactly the things a fake would get wrong.

Point TEST_DATABASE_URL at any throwaway database:

    createdb poker_test
    TEST_DATABASE_URL=postgresql://localhost/poker_test pytest

Tests that need it are skipped when it is not set, so the rest of the suite
still runs anywhere.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

# Settings are read at import time, so the environment is arranged first.
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("ALLOWED_ORIGIN", "https://example.github.io")
os.environ.setdefault("ALLOW_MISSING_ORIGIN", "1")

needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture
def table_id() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def database():
    from app.db import Database

    db = Database(TEST_DATABASE_URL)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def store(database):
    from app.store import TableStore

    s = TableStore(database)
    await s.ensure_schema()
    return s
