"""Saved states carry a format version, and a state we cannot read is refused.

Section 4.2: after a code change, old saves must still load, or the hand must
be closed out cleanly. "Cleanly" means the server says so rather than dealing
from a state it has guessed at.
"""

from __future__ import annotations

import pytest

from app import migrations
from app.config import STATE_FORMAT_VERSION
from app.migrations import UnmigratableState, migrate


def test_the_current_version_needs_no_migration():
    state = {"counter": 1}
    assert migrate(state, STATE_FORMAT_VERSION) is state


def test_a_state_from_a_newer_deploy_is_refused():
    with pytest.raises(UnmigratableState):
        migrate({}, STATE_FORMAT_VERSION + 1)


def test_a_state_with_no_migration_path_is_refused():
    with pytest.raises(UnmigratableState):
        migrate({}, STATE_FORMAT_VERSION - 1)


def test_a_registered_step_is_applied(monkeypatch):
    """Shape check for the day a real migration ships."""
    monkeypatch.setattr(migrations, "STATE_FORMAT_VERSION", 3)
    monkeypatch.setattr(
        migrations,
        "STEPS",
        {
            1: lambda s: {**s, "added_in_2": True},
            2: lambda s: {**s, "added_in_3": True},
        },
    )
    out = migrations.migrate({"old": True}, 1)
    assert out == {"old": True, "added_in_2": True, "added_in_3": True}
