"""D5 §9 31 - blocks honour `prefer_late` (D5 §5.3, D-0710).

The reference house's tank plans in 30 min blocks, and on a flat night every
block scores the same: it heated from 22:00, coasted six hours and was 0.1 K
under its ready band at 06:00 on four mornings of nine
(`design/reviews/field-audit-2026-10.md` §3). With `prefer_late` its block
ends at the deadline.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import plan_one
from tests.builders.curves import OSLO, day_bounds
from tests.core.strategies.conftest import (
    NOW,
    ORDINARY,
    TOMORROW,
    at_hours,
    flat_curve,
    flat_headroom,
)
from tests.core.strategies.test_block_and_spread import runs

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PriceCurve

TANK_W = 3000.0
BLOCK_MIN = 30
#: 300 L from 62 to 75 °C, about 1.5 h of a 3 kW element.
REQUIRED_KWH = 4.5


#: 06:00 local the morning after `NOW`: the tank's ready-by time.
DEADLINE = day_bounds(ORDINARY)[1] + timedelta(hours=6)


def _plan(source: PriceCurve, *, prefer_late: bool) -> Plan:
    return plan_one(
        source,
        required_kwh=REQUIRED_KWH,
        max_w=TANK_W,
        headroom=flat_headroom(source, TANK_W),
        now=NOW,
        horizon_end=source.slots[-1].end,
        deadline=DEADLINE,
        min_block_min=BLOCK_MIN,
        prefer_late=prefer_late,
    )


def _hours(plan: Plan) -> list[str]:
    return [
        slot.start.astimezone(OSLO).strftime("%H:%M")
        for slot in plan.slots
        if (slot.envelope_w or 0.0) > 0.0
    ]


def test_31_a_late_block_ends_at_the_deadline() -> None:
    """A flat night: 04:30-06:00 with `prefer_late`, the night's start without it."""
    source = flat_curve()
    late = _plan(source, prefer_late=True)
    early = _plan(source, prefer_late=False)

    assert late.covered
    assert early.covered
    assert late.planned_kwh == pytest.approx(REQUIRED_KWH)
    assert _hours(late) == ["04:30", "04:45", "05:00", "05:15", "05:30", "05:45"]
    assert _hours(early)[0] < "22:00", "without the flag the earliest block still wins"
    for plan in (late, early):
        for run in runs(plan):
            assert sum((slot.end - slot.start for slot in run), timedelta()) >= timedelta(
                minutes=BLOCK_MIN
            )


def test_31b_a_cheaper_hour_wins_either_way() -> None:
    """02:00-03:00 cheaper than the rest of the night is taken with or without the flag."""
    source = at_hours(flat_curve(), {2: "0.10"}, day=TOMORROW)
    for prefer_late in (True, False):
        plan = _plan(source, prefer_late=prefer_late)
        assert plan.covered
        assert {"02:00", "02:15", "02:30", "02:45"} <= set(_hours(plan))
        for run in runs(plan):
            assert sum((slot.end - slot.start for slot in run), timedelta()) >= timedelta(
                minutes=BLOCK_MIN
            )
