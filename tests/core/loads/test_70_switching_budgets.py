"""D4 §9 70 - the switching budgets per type (D-0777): the constants the level planner keeps.

A relay or element at most 24 planned starts a day, a heat pump one setpoint change an
hour, an EV four pauses a session.
"""

from __future__ import annotations

from custom_components.powerplan.core.loads.types.ev import Ev
from custom_components.powerplan.core.strategies.levels import (
    HEAT_PUMP_CHANGES_PER_HOUR,
    STARTS_PER_DAY,
)


def test_70_the_constants_per_type() -> None:
    """24 starts a day, one change an hour, four pauses a session."""
    assert STARTS_PER_DAY == 24
    assert HEAT_PUMP_CHANGES_PER_HOUR == 1
    assert Ev.max_pauses == 4
