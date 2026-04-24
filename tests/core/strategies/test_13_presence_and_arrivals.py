"""D5 §9 13, the pure part - presence lowers targets; an arrival is a deadline.

Two halves, and the other one is D4's. D4 §9 10 owns "away lowers the target and
never the floor" on the `TargetProfile` itself (INV-55,
`tests/core/loads/test_10_target_profile.py`); what is D5's is what the *planner*
then does with it:

* the plan asks the device for a lower absolute setpoint under `away` and never
  for one under the floor - banking around a lowered target is still banking;
* the change is visible to `inputs_changed`, so the plan is replaced rather than
  kept behind the hysteresis (INV-32), and `presence` is a `demand` replan
  trigger that the rate limit does not swallow;
* an arrival from the calendar is a **preheat deadline** - a fill before it, not a
  modulation around it - and it survives `vacation`, which is the cabin case the
  feature exists for (D4 §5.8).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import CalendarEvent, PresenceMode
from custom_components.powerplan.core.strategies import (
    ReplanTrigger,
    inputs_changed,
    plan_all,
    replan_due,
    should_adopt,
)
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    Weather,
    bathroom_target,
    curves_of,
    demand,
    flat_headroom,
    floor_view,
    site_ctx,
    volatile_curve,
)
from tests.core.strategies.test_08_heat_capacitor import (
    COMFORT_C,
    FLOOR_C,
    capacitor_view,
    morning_step_up,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan
    from custom_components.powerplan.core.strategies import LoadView

#: Six hours out - the cabin's owner is on the road.
ARRIVAL = NOW + timedelta(hours=6)

AWAY_DELTA = 3.0


def planned(
    *,
    presence: PresenceMode,
    view: LoadView | None = None,
    calendar: tuple[CalendarEvent, ...] = (),
) -> Plan:
    """Return the loop's `heat_capacitor` plan under a presence mode."""
    source = volatile_curve()
    load = view if view is not None else capacitor_view()
    site = site_ctx(presence=presence, calendar=calendar, forecasts=Weather(outdoor=-5.0))
    return plan_all(
        [load],
        curves_of(source),
        site,
        NOW,
        headroom=flat_headroom(source, 10_000.0),
    ).plans["loop_bath"]


def setpoints(plan: Plan, presence: PresenceMode) -> list[float]:
    """Return the absolute setpoint each slot asks for - target plus the delta."""
    profile = bathroom_target()
    return [
        profile.target(slot.start, presence)
        + (float(slot.desired_state) if isinstance(slot.desired_state, float) else 0.0)
        for slot in plan.slots
    ]


def fills(plan: Plan, source: str) -> list[Any]:
    """Return the sub-plan slots whose reason names `source` (D5 §2)."""
    return [slot for slot in plan.slots if slot.reason.endswith(f"({source})")]


def test_13_away_lowers_every_setpoint_the_plan_asks_for() -> None:
    """The plan banks around the lowered target, not around the comfort one."""
    home = planned(presence=PresenceMode.HOME)
    away = planned(presence=PresenceMode.AWAY)

    assert max(setpoints(home, PresenceMode.HOME)) == pytest.approx(
        max(setpoints(away, PresenceMode.AWAY)) + AWAY_DELTA
    )
    assert max(setpoints(away, PresenceMode.AWAY)) < COMFORT_C


def test_13_away_never_asks_for_less_than_the_floor() -> None:
    """INV-55: the floor is not a function of the hour or of who is home."""
    away = planned(presence=PresenceMode.AWAY)
    vacation = planned(presence=PresenceMode.VACATION)

    assert min(setpoints(away, PresenceMode.AWAY)) >= FLOOR_C
    assert min(setpoints(vacation, PresenceMode.VACATION)) >= FLOOR_C


def test_13_a_presence_change_is_a_changed_input_and_the_plan_is_replaced() -> None:
    """Not a price difference to be weighed against hysteresis: a new question (§5.9)."""
    home = planned(presence=PresenceMode.HOME)
    away = planned(presence=PresenceMode.AWAY)

    assert inputs_changed(home, away)
    assert should_adopt(
        home,
        away,
        site_ctx().hysteresis,
        curve=volatile_curve(),
        tz=OSLO,
        now=NOW,
        inputs_changed=inputs_changed(home, away),
    )


def test_13_presence_replans_without_waiting_for_the_rate_limit_to_pass() -> None:
    """Presence is a `demand` trigger, and the tick that follows is not swallowed."""
    assert replan_due(ReplanTrigger.DEMAND, None, NOW)
    assert replan_due(ReplanTrigger.DEMAND, NOW - timedelta(seconds=90), NOW)
    assert not replan_due(ReplanTrigger.DEMAND, NOW - timedelta(seconds=5), NOW)
    assert ReplanTrigger.DEMAND in set(ReplanTrigger)


def test_13_an_arrival_produces_a_preheat_deadline() -> None:
    """A calendar arrival is a step-up nobody scheduled: a fill before it (D5 §2)."""
    plan = planned(
        presence=PresenceMode.AWAY,
        view=capacitor_view(level_now=19.0),
        calendar=(CalendarEvent(start=ARRIVAL, end=ARRIVAL + timedelta(hours=4), summary="home"),),
    )
    preheat = fills(plan, "arrival")

    assert preheat, "the arrival is a deadline"
    assert all(slot.start < ARRIVAL for slot in preheat)
    assert plan.deadline == ARRIVAL
    assert sum(slot.kwh for slot in preheat) > 0.0


def test_13_an_arrival_is_a_deadline_on_vacation_too() -> None:
    """The cabin case: nobody is there to be warm for until somebody arrives (D4 §5.8)."""
    calendar = (CalendarEvent(start=ARRIVAL, end=ARRIVAL + timedelta(hours=4)),)
    stepped = capacitor_view(
        level_now=19.0,
        target=bathroom_target(schedule=morning_step_up(), comfort_default=FLOOR_C),
    )
    plan = planned(presence=PresenceMode.VACATION, view=stepped, calendar=calendar)

    assert fills(plan, "arrival")
    assert not fills(plan, "schedule"), "a schedule step-up is not a deadline on vacation"


def test_13_no_arrival_and_no_step_up_is_no_deadline_at_all() -> None:
    """Without either, the plan is pure modulation and has no requirement (§5.7)."""
    plan = planned(presence=PresenceMode.HOME)

    assert plan.deadline is None
    assert plan.required_kwh is None
    assert not fills(plan, "arrival")


def test_13_presence_is_part_of_the_fills_own_inputs_digest() -> None:
    """A `deadline_fill` load re-decides on presence as well (§5.9, D-0136)."""
    source = volatile_curve()
    view = floor_view(demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=960.0))
    home = plan_all([view], curves_of(source), site_ctx(presence=PresenceMode.HOME), NOW)
    away = plan_all([view], curves_of(source), site_ctx(presence=PresenceMode.AWAY), NOW)

    assert home.plans["loop_bath"].inputs_hash != away.plans["loop_bath"].inputs_hash
