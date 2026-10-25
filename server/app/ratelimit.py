"""A token bucket, used per WebSocket connection."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    capacity: int
    per_second: float
    tokens: float = 0.0
    updated: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    def take(self, amount: float = 1.0, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        elapsed = max(0.0, now - self.updated)
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.per_second)
        if self.tokens < amount:
            return False
        self.tokens -= amount
        return True
