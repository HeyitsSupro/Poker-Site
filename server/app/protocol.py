"""The wire protocol.

JSON over one WebSocket. Every message is an object with a `t` (type) field.

Client -> server
  {"t":"hello","table":<id>,"token":<str|null>,"nick":<str>,"protocol":1,
   "ack":<seq>}      first message on every connection, including reconnects
  {"t":"ping","at":<ms>}                                   client heartbeat
  {"t":"action","aid":<uuid>,"kind":<str>,"data":{...}}    a player action
  {"t":"resync"}                                 "send me the whole state"

Server -> client
  {"t":"welcome","protocol":1,"player_id":...,"token":...,
   "heartbeat":<seconds>,"table":<id>}
  {"t":"state","seq":<n>,"version":<n>,"state":{...}}  full per-viewer view
  {"t":"events","events":[...]}                       appended log entries
  {"t":"pong","at":<ms>}
  {"t":"error","code":<str>,"message":<str>,"aid":<uuid|null>}  human-readable
  {"t":"replaced"}      another device connected as this player (phase 1)

Design notes
  * The server sends a full per-viewer state snapshot rather than diffs. A
    table state is a few kilobytes, and resending it removes a whole class of
    desync bugs — important when phones drop in and out of signal.
  * Each snapshot is built for one viewer, so hidden information is filtered
    at the point it is serialised, never at the point it is rendered.
  * Errors always carry a reason the UI can show verbatim.
"""

from __future__ import annotations

import json
from typing import Any

from .config import PROTOCOL_VERSION

MAX_NICK_LENGTH = 20
MAX_CHAT_LENGTH = 300


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, aid: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.aid = aid


def parse(raw: str, max_bytes: int) -> dict[str, Any]:
    if len(raw.encode("utf-8", "ignore")) > max_bytes:
        raise ProtocolError("too_large", "That message was too large.")
    try:
        msg = json.loads(raw)
    except ValueError:
        raise ProtocolError("bad_json", "That message was not valid JSON.") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
        raise ProtocolError("bad_message", "That message had no type.")
    return msg


def check_protocol(msg: dict[str, Any]) -> None:
    version = msg.get("protocol", PROTOCOL_VERSION)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            "protocol_mismatch",
            "This page is out of date. Reload to get the latest version.",
        )


def clean_text(value: Any, limit: int, fallback: str = "") -> str:
    """Strip control characters and clamp length.

    Everything a player types is a label, never markup: the client renders it
    as text, and this keeps anything surprising out of the log and the
    database.
    """
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] or fallback


def error(code: str, message: str, aid: str | None = None) -> dict[str, Any]:
    return {"t": "error", "code": code, "message": message, "aid": aid}
