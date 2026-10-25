"""The thing that makes deadlines happen.

Everything else in the server moves because somebody sent a message. Three
things move because time passed: a player's clock runs out, the ten seconds
to choose a number of runs end, and a showdown decision goes unmade. The
ticker is what turns those into ordinary actions.

It holds one sleeping task per table, waking at that table's next deadline
and submitting a `tick`. There is no polling loop: a table with nothing
pending costs nothing, which matters on a free instance.

The state it schedules from is the saved state, so a restart rebuilds every
pending timer from the database rather than losing it. A tick carries an
action id derived from the deadline it is answering, so the same deadline
can never be applied twice — two servers, a retry and a restart all collapse
to one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from . import clock

log = logging.getLogger("ticker")

# Never sleep longer than this in one go, so a clock that was set before a
# long suspend is re-examined rather than slept straight through.
MAX_SLEEP = 60.0
# A small cushion so we wake just after the deadline, not a hair before.
OVERSHOOT = 0.05


def tick_action_id(table_id: str, deadline: dict) -> str:
    """One id per deadline, so a repeated tick is recognised as a replay."""
    at = deadline["at"]
    return f"tick:{table_id}:{deadline['kind']}:{at:.3f}"


class Ticker:
    def __init__(self, fire: Callable[[str], Awaitable[None]]) -> None:
        # `fire` submits a tick for one table; main.py supplies it.
        self._fire = fire
        self._tasks: dict[str, asyncio.Task] = {}

    def schedule(self, table_id: str, state: dict) -> None:
        """Arrange to wake this table at its next deadline, if it has one."""
        self.schedule_at(table_id, clock.next_wake(state))

    def schedule_at(self, table_id: str, at: float | None) -> None:
        """Same, but given the time directly.

        Used at start-up, where the deadlines are read straight out of the
        database without loading each table's whole state.
        """
        self.cancel(table_id)
        if at is None:
            return
        self._tasks[table_id] = asyncio.create_task(
            self._wait_then_fire(table_id, float(at)),
            name=f"tick:{table_id}",
        )

    def cancel(self, table_id: str) -> None:
        task = self._tasks.pop(table_id, None)
        if task is not None and not task.done():
            task.cancel()

    def cancel_all(self) -> None:
        for table_id in list(self._tasks):
            self.cancel(table_id)

    async def _wait_then_fire(self, table_id: str, at: float) -> None:
        try:
            while True:
                remaining = at - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining + OVERSHOOT, MAX_SLEEP))
            await self._fire(table_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed tick must not kill the loop
            log.exception("tick for %s failed: %s", table_id, exc)
