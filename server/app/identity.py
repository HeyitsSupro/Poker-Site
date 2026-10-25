"""Who is who, without accounts (section 4.5).

A player is a record on the table: a nickname, a held stack, a ledger, and
two secrets. The seat is separate and temporary. Standing up, closing the
app, losing signal or coming back three days later all land on the same
record, because the record is never keyed on the connection or the seat.

The two secrets:

* a **player token**, created at first join and kept in that browser. It is
  what makes "open the link again" just work.
* a **rejoin link**, shown only to that player, for moving to another device.
  Regenerating one invalidates the old one.

Both are stored hashed and never sent to anyone else or written to the log.
A nickname proves nothing: claiming an existing player always needs a secret
or the host's say-so.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

# 256 bits. Section 4.5 asks for at least 128.
TOKEN_BYTES = 32
MAX_NICK = 20


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_secret() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_player_id() -> str:
    return secrets.token_urlsafe(8)


def matches(stored_hash: str | None, offered: str | None) -> bool:
    """Constant-time comparison, tolerant of missing values."""
    if not stored_hash or not offered:
        return False
    return secrets.compare_digest(stored_hash, hash_secret(offered))


def new_player(
    player_id: str,
    nick: str,
    token: str,
    rejoin: str,
    *,
    is_host: bool = False,
) -> dict[str, Any]:
    return {
        "id": player_id,
        "nick": nick,
        "token_hash": hash_secret(token),
        "rejoin_hash": hash_secret(rejoin),
        "joined_at": now_iso(),
        "is_host": is_host,
        "is_co_host": False,
        "revoked": False,
        # Chips that belong to the player but are not in play.
        "held": 0,
        # Ledger counters. Resuming a held stack must never change these.
        "buy_in_total": 0,
        "buy_in_count": 0,
        "cash_out_total": 0,
        # Badges beside the name.
        "hands_won": 0,
        "rebuys": 0,
    }


def find_by_token(state: dict, token: str | None) -> str | None:
    for player_id, player in state.get("players", {}).items():
        if player.get("revoked"):
            continue
        if matches(player.get("token_hash"), token):
            return player_id
    return None


def find_by_rejoin(state: dict, rejoin: str | None) -> str | None:
    for player_id, player in state.get("players", {}).items():
        if player.get("revoked"):
            continue
        if matches(player.get("rejoin_hash"), rejoin):
            return player_id
    return None


def is_host(state: dict, player_id: str | None) -> bool:
    player = state.get("players", {}).get(player_id or "")
    if not player:
        return False
    return bool(player.get("is_host") or player.get("is_co_host"))


def host_code_matches(state: dict, code: str | None) -> bool:
    return matches(state.get("host_code_hash"), code)


def public_player(player: dict, viewer_id: str | None) -> dict[str, Any]:
    """What everyone at the table may know about a player.

    No hashes, no secrets — not even for the player themselves. A player's
    own rejoin link is handed over once, when it is created or regenerated,
    and is never part of a state snapshot.
    """
    return {
        "id": player["id"],
        "nick": player["nick"],
        "joined_at": player.get("joined_at"),
        "is_host": bool(player.get("is_host")),
        "is_co_host": bool(player.get("is_co_host")),
        "held": player.get("held", 0),
        "hands_won": player.get("hands_won", 0),
        "rebuys": player.get("rebuys", 0),
        "you": player["id"] == viewer_id,
    }
