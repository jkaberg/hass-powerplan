"""`schedule` - powersaver's Fixed Schedule, and the profile already written.

Not a numbered §9 item: D5 §9 has no row for `schedule`, and PLAN's WP4.1 row
names items 5, 9 and 15. What is pinned here is the contract the rest of the
roster shares - local evaluation, `0` outside the window rather than a shed, and a
`MODE` load feeling the window as an option - plus the thing that makes the
strategy worth having: a household that has already written its hours into a
weekly table or a bound HA `schedule.*` helper does not type them again (INV-66).
"""

from __future__ import annotations

from datetime import time, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import Mode, WeeklyRow, WeeklyTable
from custom_components.powerplan.core.model import Desired, PlanMode
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.schedule import Window
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    bathroom_target,
    curves_of,
    demand,
    ev_view,
    floor_view,
    site_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan

FLOOR_W = 960.0

#: Every day, 06:00–08:00 local - the school-morning towel rail.
MORNING = (-1, 6 * 60, 8 * 60)
#: Every day, 22:00–06:00 local: a window that wraps past midnight.
NIGHT = (-1, 22 * 60, 6 * 60)


def planned(windows: Any = (), **kwargs: Any) -> Plan:
    """Return the loop's `schedule` plan over the two-day NO3 curve."""
    source = volatile_curve()
    options: dict[str, Any] = {
        "strategy": "schedule",
        "params": {"windows": windows},
        "demand": demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=FLOOR_W),
    }
    options.update(kwargs)
    return plan_all([floor_view(**options)], curves_of(source), site_ctx(), NOW).plans["loop_bath"]


def hours_on(plan: Plan) -> list[int]:
    """Return the local hours the plan runs in, over the whole horizon."""
    return sorted(
        {slot.start.astimezone(OSLO).hour for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0}
    )


def test_a_window_is_the_whole_answer_and_the_price_is_not_consulted() -> None:
    """On inside, `0` outside - and the expensive morning runs anyway (D5 §6)."""
    plan = planned([MORNING])

    assert plan.mode is PlanMode.PRICE
    assert hours_on(plan) == [6, 7]
    assert all((slot.envelope_w or 0.0) in (0.0, FLOOR_W) for slot in plan.slots)
    assert "window" in plan.reason


def test_a_window_may_wrap_past_local_midnight() -> None:
    """`(-1, 1320, 360)` is 22:00–06:00, which is two local days of one window."""
    plan = planned([NIGHT])

    assert hours_on(plan) == [0, 1, 2, 3, 4, 5, 22, 23]


def test_a_weekday_window_only_runs_on_its_own_weekday() -> None:
    """`weekday` 0 is Monday; the horizon here is a Thursday and a Friday."""
    thursday, friday = 3, 4
    on_friday = planned([(friday, 9 * 60, 11 * 60)])
    on_monday = planned([(0, 9 * 60, 11 * 60)])

    assert hours_on(on_friday) == [9, 10]
    assert NOW.astimezone(OSLO).weekday() == thursday
    assert hours_on(on_monday) == []


def test_the_slots_outside_the_window_stand_still_rather_than_shed() -> None:
    """A planned `0` is "stand still" and never a shed (INV-25, INV-30)."""
    plan = planned([MORNING])
    outside = [slot for slot in plan.slots if slot.envelope_w == 0.0]

    assert outside
    assert all(slot.kwh == 0.0 for slot in outside)
    assert all(slot.reason == "outside the schedule" for slot in outside)


def test_a_mode_load_feels_the_window_as_an_option() -> None:
    """A thermostat cannot be capped, only re-targeted (D4 §5.5)."""
    plan = planned([MORNING], kind="mode")
    inside = [slot for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]
    outside = [slot for slot in plan.slots if slot.envelope_w == 0.0]

    assert all(slot.desired_state is Desired.COMFORT for slot in inside)
    assert all(slot.desired_state is Desired.SHED for slot in outside)


def test_a_switch_load_gets_no_second_lever() -> None:
    """A `SWITCH` or `MODULATE` load has only the envelope (D4 §5.3, §5.6)."""
    source = volatile_curve()
    view = ev_view(strategy="schedule", params={"windows": [MORNING]})
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert all(slot.desired_state is None for slot in plan.slots)


def test_without_windows_the_loads_own_profile_is_the_schedule() -> None:
    """A weekly table the household already wrote is the schedule (INV-66)."""
    stepped = bathroom_target(
        schedule=WeeklyTable(
            zone=OSLO,
            default=21.0,
            rows=tuple(
                row
                for day in range(7)
                for row in (
                    WeeklyRow(weekday=day, start=time(6, 0), value=24.0),
                    WeeklyRow(weekday=day, start=time(9, 0), value=21.0),
                )
            ),
        ),
        comfort_default=21.0,
    )
    plan = planned(target=stepped)

    assert hours_on(plan) == [6, 7, 8]
    assert plan.reason == "the load's own schedule"


def test_a_constant_profile_and_no_windows_is_not_a_schedule() -> None:
    """One target all week asks for nothing: the plan has no opinion (INV-30)."""
    plan = planned()

    assert plan.slots
    assert all(slot.envelope_w == 0.0 for slot in plan.slots)


def test_a_load_with_neither_windows_nor_a_profile_says_so() -> None:
    """No schedule at all is a plan that stands aside, not one of zeroes (D5 §8)."""
    source = volatile_curve()
    view = ev_view(strategy="schedule")
    plan = plan_all([view], curves_of(source), site_ctx(), NOW).plans["ev"]

    assert plan.mode is PlanMode.NONE
    assert plan.cap_w(NOW) is None
    assert plan.reason == "no schedule"


def test_a_forced_load_is_left_to_the_allocator() -> None:
    """Force is the household's decision, whatever the schedule says (D-0138)."""
    plan = planned([MORNING], mode=Mode.FORCE)

    assert plan.mode is PlanMode.FORCE
    assert plan.cap_w(NOW) is None


def test_the_window_type_parses_a_configured_row() -> None:
    """The one place a scheduled window is parsed (D5 §6)."""
    assert Window.of((1, 60, 120)) == Window(weekday=1, start_min=60, end_min=120)
    assert Window.of(Window(weekday=-1, start_min=0, end_min=60)).weekday == -1


def test_the_planned_energy_is_the_window_at_full_power() -> None:
    """`max_w` for every hour of the window, read off the slots (INV-7)."""
    plan = planned([MORNING])
    running = [slot for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]

    assert plan.planned_kwh == pytest.approx(FLOOR_W / 1000.0 * 0.25 * len(running))
    assert len(running) == 8, "two hours of quarter slots, one horizon day"


def test_the_horizon_holds_one_morning_because_the_curve_stops(  # noqa: D103
) -> None:
    plan = planned([MORNING])
    days = {slot.start.astimezone(OSLO).date() for slot in plan.slots if (slot.envelope_w or 0) > 0}

    assert len(days) == 1
    assert plan.slots[-1].end <= NOW + timedelta(hours=48)
