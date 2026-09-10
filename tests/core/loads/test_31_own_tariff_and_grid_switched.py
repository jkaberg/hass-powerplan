"""D4 §9 31–33 - a load on its own grid tariff, one the grid switches, one with unknown times.

31 (G13): a heat pump bound to a §14a Modul 3 tariff plans on its own curve, the
house's other loads on the house's. 32 (G14): an HDO water heater is granted
nothing outside its windows at any stage, a comfort floor's included, and the
reason is `grid_switched`. 33 (G15): a controlled circuit whose times are not
published is `delegated` - never written, reserved only while it draws - and D11
counts its cost like any delegated load's (D11 §9 14).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    GridSwitched,
    allocate,
)
from custom_components.powerplan.core.allocation.reserved import reserved_w
from custom_components.powerplan.core.loads import Action
from custom_components.powerplan.core.model import Mode
from custom_components.powerplan.core.pricing.holidays import NO_HOLIDAYS
from custom_components.powerplan.core.strategies import Curves, LoadView, plan_all
from custom_components.powerplan.core.tariffs import TimeFilter
from tests.core.allocation.conftest import alloc_ctx, budget_of, comfort, controlled, demand
from tests.core.loads.conftest import OSLO, grant, load_ctx, load_from, load_state
from tests.core.strategies.conftest import NOW as PLAN_NOW
from tests.core.strategies.conftest import curves_of, flat_curve, site_ctx

#: HDO: the relay closes 00–03 and 13–15 every day.
HDO = (TimeFilter(hours=((0, 3 * 60),)), TimeFilter(hours=((13 * 60, 15 * 60),)))


def _shifted(cheap: range):  # type: ignore[no-untyped-def]
    base = flat_curve(days=2)
    extra = Decimal("0.5")
    return replace(
        base,
        slots=tuple(
            slot
            if slot.start.astimezone(OSLO).hour in cheap
            else replace(slot, total=slot.total + extra, components={"spot": slot.total + extra})
            for slot in base.slots
        ),
    )


def test_31_a_modul_3_heat_pump_plans_on_its_own_curve_and_the_house_on_its() -> None:
    """The binding travels subentry → `LoadConfig` → `LoadView`; each load its own curve."""
    pump = load_from("heat_pump", load_id="hp")
    pump = replace(pump, config=replace(pump.config, grid_tariff="modul3"))
    assert LoadView.of(pump, demand()).grid_tariff == "modul3"
    assert load_from("water_heater").config.grid_tariff is None

    wants = {"strategy": "cheapest_hours", "params": {"hours_per_day": 4}}
    view = replace(LoadView.of(pump, demand(required_kwh=8.0, max_w=2000.0)), **wants)
    other = LoadView(load_id="ev", priority=5, demand=demand(max_w=2000.0), **wants)
    curves = Curves(
        import_=curves_of(_shifted(range(12, 16))).import_,
        per_load={"modul3": _shifted(range(1, 5))},
    )
    plans = plan_all([view, other], curves, site_ctx(), PLAN_NOW).plans
    day = PLAN_NOW.astimezone(OSLO).date() + timedelta(days=1)

    def hours(load_id: str) -> set[int]:
        return {
            slot.start.astimezone(OSLO).hour
            for slot in plans[load_id].slots
            if slot.kwh > 0 and slot.start.astimezone(OSLO).date() == day
        }

    assert hours("hp") == set(range(1, 5))
    assert hours("ev") == set(range(12, 16))


def _tank(**kwargs: object) -> LoadView:
    options: dict[str, object] = {
        "load_id": "tank",
        "priority": 20,
        "strategy": "deadline_fill",
        "demand": demand(max_w=3000.0),
        "nameplate_w": 3000.0,
        "kind": "switch",
        "allowed": HDO,
    }
    options.update(kwargs)
    return LoadView(**options)  # type: ignore[arg-type]


@pytest.mark.parametrize("stage", [0, 3, 4])
def test_32_an_hdo_water_heater_is_granted_nothing_outside_its_windows(stage: int) -> None:
    """At 18:07 the relay is open: 0 W at every stage, a violated floor included."""
    violated = demand(
        max_w=3000.0,
        comfort=comfort(current=30.0, floor=45.0, violated=True),
        price_sensitive=False,
    )
    for tank in (_tank(), _tank(demand=violated)):
        ctx = alloc_ctx([tank], budget=budget_of(20_000.0), stage=stage, blunt=stage == 4)
        grants, _, _ = allocate(ctx, (GridSwitched(OSLO, NO_HOLIDAYS),), AllocCfg(), AllocState())
        assert grants["tank"].w == 0.0
        # Wanted power: held back *for* the grid's relay; a floor: capped by it.
        held = grants["tank"]
        assert held.shed_reason == "grid_switched" or held.capped_by == ("grid_switched",)


def test_32_inside_its_window_it_is_granted_as_any_load() -> None:
    """At 13:30 the relay is closed: the tank gets its 3 kW."""
    tank = _tank()
    at = alloc_ctx([tank]).now.replace(hour=13, minute=30)
    ctx = alloc_ctx([tank], budget=budget_of(20_000.0), now=at)
    grants, _, _ = allocate(ctx, (GridSwitched(OSLO, NO_HOLIDAYS),), AllocCfg(), AllocState())
    assert grants["tank"].w == pytest.approx(3000.0)


def test_33_a_circuit_with_unknown_times_is_delegated_and_never_written() -> None:
    """`switched = unknown` resolves to no windows: the load is `delegated` whatever its mode."""
    load = load_from("water_heater")
    load = replace(load, config=replace(load.config, switched="unknown", allowed=()))
    assert load.mode_now(load_state(), load_ctx()) is Mode.DELEGATED
    assert load.mode_now(load_state(mode=Mode.OFF), load_ctx()) is Mode.OFF
    view = LoadView.of(load, demand(max_w=3000.0), mode=Mode.DELEGATED)
    assert not view.plans
    _, result = load.apply(grant(3000.0), load_state(), load_ctx())
    assert result.action in {Action.DELEGATED, Action.SAME}
    assert not result.written


def test_33_it_is_reserved_while_it_draws_and_not_while_the_relay_is_open() -> None:
    """Nameplate while drawing; nothing while it measures idle; nameplate while unmeasured."""
    view = _tank(allowed=(), mode=Mode.DELEGATED)
    assert reserved_w(view, 0.0, controlled("tank", measured_w=2950.0)) == 3000.0
    assert reserved_w(view, 0.0, controlled("tank", measured_w=0.0)) == 0.0
    assert reserved_w(view, 0.0, controlled("tank", measured_w=None)) == 3000.0
    assert reserved_w(_tank(mode=Mode.DELEGATED), 0.0, controlled("tank", measured_w=0.0)) == 3000.0
