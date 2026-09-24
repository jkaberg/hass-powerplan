"""D5 §9 28–29 - the free slot is self-use, and a hold keeps what a later slot needs.

A battery's `None` slot is the inverter's own self-use, which spends the charge
on the house's own load (INV-30, D4 §4.2). Before WP7.9 the forward simulation
counted a free slot as idle, so an evening discharge could be planned on energy
self-use had already spent by then (PLAN §9 "Open items" 10). These tests replay
each plan the way the battery will run it - self-use in `None`, nothing out in
`0` - and check that every planned discharge still finds its energy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import Headroom, get
from tests.core.strategies.conftest import (
    battery_store,
    battery_view,
    flat_curve,
    plan_ctx,
    volatile_curve,
)

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.powerplan.core.model import Plan

PARAMS = {"threshold": Decimal("0.05"), "round_trip_eff": 0.95 * 0.95}
BASELINE_W = 2000.0


@dataclass(frozen=True)
class Baseline:
    """D10's answers for a house that draws a steady 2 kW and has no panels."""

    watts: float = BASELINE_W

    def outdoor_c(self, t: datetime) -> float | None:
        """No weather: the house's load is its baseline."""
        del t
        return None

    def surplus_w(self, t: datetime) -> float:
        """No panels."""
        del t
        return 0.0

    def baseline_w(self, t: datetime) -> float:
        """Return a steady draw all day."""
        del t
        return self.watts

    def hold_w(self, load_id: str, t: datetime) -> float | None:
        """No thermal load holds anything here."""
        del load_id, t
        return None


def _run(plan: Plan, *, level: float) -> list[tuple[float, float]]:
    """Replay `plan` as the battery runs it: `(asked, delivered)` per planned discharge."""
    store = battery_store()
    per_unit = store.capacity_kwh_per_unit()
    reserve = store.reserve_soc or 20.0
    soc = level
    out: list[tuple[float, float]] = []
    for slot in plan.slots:
        hours = (slot.end - slot.start).total_seconds() / 3600.0
        w = slot.envelope_w
        if w is None:
            w = -min(BASELINE_W, store.max_discharge_w)  # self-use covers the house
            asked = 0.0
        else:
            asked = -w if w < 0.0 else 0.0
        if w > 0.0:
            soc = min(store.max_soc, soc + w * hours / 1000.0 * store.charge_eff / per_unit)
        elif w < 0.0:
            room = max(0.0, (soc - reserve) * per_unit * store.discharge_eff)
            delivered = min(-w, room / hours * 1000.0)
            soc -= delivered * hours / 1000.0 / store.discharge_eff / per_unit
            if asked > 0.0:
                out.append((asked, delivered))
    return out


def _plan(strategy: str = "arbitrage", *, level: float = 50.0, **params: object) -> Plan:
    source = volatile_curve()
    view = battery_view(strategy=strategy, level_now=level)
    ctx = plan_ctx(source, load=view, headroom=Headroom(by_slot={}), forecasts=Baseline())
    return get(strategy).plan(view.demand, ctx, {**PARAMS, **params})


@pytest.mark.inv("INV-30")
def test_28_the_free_slots_before_a_planned_discharge_hold() -> None:
    """Charged cheaply, the battery holds through the day and discharges into the evening peak."""
    plan = _plan()

    holds = [slot for slot in plan.slots if slot.envelope_w == 0.0]
    discharges = _run(plan, level=50.0)

    assert discharges, "an evening peak is worth discharging into"
    assert holds, "self-use would spend the charge before the peak"
    assert all(slot.reason == "hold" for slot in holds)
    for asked, delivered in discharges:
        assert delivered == pytest.approx(asked, abs=1.0), (
            "every planned discharge finds its energy"
        )


def test_28_a_battery_that_cannot_hold_takes_only_pairs_self_use_leaves_whole() -> None:
    """Without a hold, no `0` slot, and no discharge planned on energy self-use spends first."""
    plan = _plan(can_hold=False)

    assert not [slot for slot in plan.slots if slot.envelope_w == 0.0]
    for asked, delivered in _run(plan, level=50.0):
        assert delivered == pytest.approx(asked, abs=1.0)


def test_28_a_battery_it_cannot_tell_to_discharge_plans_its_discharge_as_self_use() -> None:
    """No discharge command: the peak is `None` (self-use serves it), held for until then."""
    plan = _plan(can_discharge=False)

    assert not [slot for slot in plan.slots if (slot.envelope_w or 0.0) < 0.0]
    assert [slot for slot in plan.slots if slot.envelope_w == 0.0], "the charge is kept for it"


def test_29_with_nothing_committed_every_slot_is_self_use() -> None:
    """A flat curve plans no trade, so nothing needs holding: the whole plan is `None`."""
    view = battery_view(strategy="arbitrage", level_now=60.0)
    ctx = plan_ctx(flat_curve(), load=view, headroom=Headroom(by_slot={}), forecasts=Baseline())
    plan = get("arbitrage").plan(view.demand, ctx, PARAMS)

    assert plan.slots
    assert all(slot.envelope_w is None for slot in plan.slots)


def test_29_the_sun_left_fills_a_free_slot_in_the_simulation() -> None:
    """Surplus in a free slot charges the simulated battery, so a later discharge has it."""
    source = volatile_curve()
    view = battery_view(strategy="arbitrage", level_now=21.0)
    slots = source.slots_between(source.slots[0].start, source.slots[-1].end)
    sunny = {slot.start: 3000.0 for slot in slots if 10 <= slot.start.hour < 15}
    ctx = plan_ctx(
        source, load=view, headroom=Headroom(by_slot={}), forecasts=Baseline(), surplus=sunny
    )
    plan = get("arbitrage").plan(view.demand, ctx, {**PARAMS, "allow_grid_charge": False})

    assert [slot for slot in plan.slots if (slot.envelope_w or 0.0) < 0.0], (
        "the sun banked at noon is discharged into the evening peak"
    )
