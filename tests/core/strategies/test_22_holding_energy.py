"""D5 §9 22 - what holding a floor at its target costs is in the plan (D-0501).

A floor at its target draws power all day, but its plan said 0 kWh, so the lanes and the
Plan card were empty. Now every slot a thermal strategy leaves the thermostat to hold
carries the standing loss: from the store's loss coefficient where one is known, else
from the loop's measured holding draw; a coasting or postponed slot still carries
nothing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads.stores import SlabStore
from custom_components.powerplan.core.strategies import plan_all
from custom_components.powerplan.core.strategies.holding import holding_kwh
from tests.core.strategies.conftest import (
    NOW,
    Weather,
    bathroom_target,
    curves_of,
    demand,
    floor_view,
    plan_ctx,
    site_ctx,
    volatile_curve,
)
from tests.core.strategies.test_08_heat_capacitor import capacitor_view
from tests.core.strategies.test_08_heat_capacitor import planned as capacitor_planned

QUARTER = timedelta(minutes=15)


def _ctx(loss: float | None, weather: Weather) -> object:
    store = SlabStore(area_m2=12.0, screed_mm=40.0, loss_coeff_w_per_k=loss, max_c=27.0)
    return plan_ctx(
        volatile_curve(),
        load=floor_view(store=store, target=bathroom_target()),
        forecasts=weather,
    )


def test_22a_a_known_loss_coefficient_prices_the_hold() -> None:
    """20 W/K from 24 °C to 4 °C outside is 400 W: 0.1 kWh a quarter hour."""
    ctx = _ctx(20.0, Weather(outdoor=4.0, hold={"loop_bath": 999.0}))
    assert holding_kwh(ctx, NOW, NOW + QUARTER, 24.0) == pytest.approx(0.1)  # type: ignore[arg-type]


def test_22b_without_one_the_measured_draw_does() -> None:
    """No coefficient (a slab's fit usually fails its bounds): the loop's own 300 W."""
    ctx = _ctx(None, Weather(outdoor=4.0, hold={"loop_bath": 300.0}))
    assert holding_kwh(ctx, NOW, NOW + QUARTER, 24.0) == pytest.approx(0.075)  # type: ignore[arg-type]
    assert holding_kwh(_ctx(None, Weather(outdoor=4.0)), NOW, NOW + QUARTER, 24.0) == 0.0  # type: ignore[arg-type]


def test_22c_heat_capacitor_holds_with_energy_and_coasts_without() -> None:
    """Every slot but a coast carries the loop's holding draw; a coast carries none."""
    weather = Weather(outdoor=-5.0, hold={"loop_bath": 400.0})
    plan = capacitor_planned(view=capacitor_view(), forecasts=weather)
    before = capacitor_planned(view=capacitor_view(), forecasts=Weather(outdoor=-5.0))
    for slot in plan.slots:
        if slot.envelope_w == 0.0:
            assert slot.hold_kwh == 0.0
        else:
            assert slot.hold_kwh >= 400.0 * slot.hours / 1000.0 - 1e-9
    # Holding is priced and kept apart: it never counts as moving the store (coverage).
    assert plan.planned_kwh == pytest.approx(before.planned_kwh)
    assert plan.hold_kwh > 5.0
    assert plan.cost_estimate.amount > before.cost_estimate.amount


def test_22d_best_save_holds_where_it_is_free_and_not_where_it_waits() -> None:
    """A free slot is the loop's holding draw; a postponed one is off and 0 kWh."""
    source = volatile_curve()
    view = floor_view(
        strategy="best_save",
        target=bathroom_target(),
        demand=demand(required_kwh=3.0, deadline=None, min_w=0.0, max_w=960.0),
    )
    site = site_ctx(forecasts=Weather(outdoor=-5.0, hold={"loop_bath": 400.0}))
    plan = plan_all([view], curves_of(source), site, NOW).plans["loop_bath"]
    free = [slot for slot in plan.slots if slot.envelope_w is None]
    postponed = [slot for slot in plan.slots if slot.envelope_w == 0.0]
    assert free
    assert postponed
    assert all(slot.hold_kwh == pytest.approx(400.0 * slot.hours / 1000.0) for slot in free)
    assert all(slot.hold_kwh == 0.0 and slot.kwh == 0.0 for slot in postponed)
