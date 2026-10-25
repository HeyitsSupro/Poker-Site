"""Runtime configuration, read from environment variables.

Nothing secret is ever hard-coded here. On Render these come from the
service's Environment settings; locally they come from your shell or a
`.env` you never commit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Bumped whenever the shape of a saved table state changes in a way that
# needs migrating. Persisted with every saved state (see app/migrations.py).
STATE_FORMAT_VERSION = 1

# Bumped on breaking wire-protocol changes. The client sends the version it
# speaks; the server refuses anything it does not understand rather than
# guessing.
PROTOCOL_VERSION = 1


def _split(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    # Comma-separated list. In production this is the GitHub Pages address.
    allowed_origins: list[str] = field(default_factory=list)
    # Allow connections that send no Origin header at all (curl, native test
    # clients, the terminal bots in phase 1). Browsers always send one, so
    # this does not weaken the browser-facing check.
    allow_missing_origin: bool = True
    port: int = 8000
    # Client heartbeat cadence, echoed to the client on connect so there is
    # one source of truth. Comfortably under Render's 15-minute idle window
    # even when a phone throttles background timers.
    heartbeat_seconds: int = 120
    # Drop a socket that has gone quiet for this long. Must be several
    # heartbeats so a throttled background tab is not killed needlessly.
    idle_timeout_seconds: int = 600
    # Token bucket per connection: burst size, and refill per second.
    rate_burst: int = 40
    rate_per_second: float = 8.0
    max_message_bytes: int = 16 * 1024
    # How long a replayed action id is remembered. Comfortably longer than
    # any plausible disconnect.
    dedupe_retention_hours: int = 48

    @property
    def has_database(self) -> bool:
        return bool(self.database_url)


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        allowed_origins=_split(os.environ.get("ALLOWED_ORIGIN", "")),
        allow_missing_origin=os.environ.get("ALLOW_MISSING_ORIGIN", "1") != "0",
        port=int(os.environ.get("PORT", "8000")),
        heartbeat_seconds=int(os.environ.get("HEARTBEAT_SECONDS", "120")),
        idle_timeout_seconds=int(os.environ.get("IDLE_TIMEOUT_SECONDS", "600")),
    )


settings = load_settings()
