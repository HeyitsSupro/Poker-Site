"""State format versioning.

Every saved table state carries the format version it was written with. When
the code changes shape, add a step here. A state that cannot be migrated is
not silently loaded: the caller is told, and closes the table out cleanly
rather than dealing from a state it does not understand.
"""

from __future__ import annotations

from collections.abc import Callable

from .config import STATE_FORMAT_VERSION

State = dict


class UnmigratableState(Exception):
    """Raised when a saved state cannot be brought up to the current format."""


# version N -> N+1. Empty until the first breaking change ships.
STEPS: dict[int, Callable[[State], State]] = {}


def migrate(state: State, from_version: int) -> State:
    if from_version == STATE_FORMAT_VERSION:
        return state
    if from_version > STATE_FORMAT_VERSION:
        raise UnmigratableState(
            f"saved state is format v{from_version}, this server speaks "
            f"v{STATE_FORMAT_VERSION} (a newer deploy wrote it)"
        )
    version = from_version
    while version < STATE_FORMAT_VERSION:
        step = STEPS.get(version)
        if step is None:
            raise UnmigratableState(
                f"no migration from format v{version} to v{version + 1}"
            )
        state = step(state)
        version += 1
    return state
