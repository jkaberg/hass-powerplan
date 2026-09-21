"""D5 §9 17 - the effective curve: surplus bands, the walk takes them, no panels changes nothing.

Phase 7 (D5 §2). A slot's watts are priced in the order a load draws them:
surplus above the export limit at 0, the rest of the surplus at what its export
would earn, the grid after that. The loads take the surplus in INV-33's order,
as they take the room, and a site without a production forecast plans exactly
as it did before the effective curve existed.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import Carrier, PriceCurve, Slot
from custom_components.powerplan.core.strategies import Curves, plan_all
from custom_components.powerplan.core.strategies.deadline_fill import (
    _Candidate,
    _candidate,
    _fill_priced,
)
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    TOMORROW,
    Sun,
    Weather,
    curves_of,
    demand,
    ev_view,
    export_of,
    flat_curve,
    flat_headroom,
    floor_view,
    plan_ctx,
    site_ctx,
    volatile_curve,
)

#: Noon to three tomorrow, 3 kW of surplus.
MIDDAY = {11: 3000.0, 12: 3000.0, 13: 3000.0}


def _noon_slot(source: PriceCurve) -> Slot:
    return next(
        slot
        for slot in source.slots
        if slot.start.astimezone(OSLO).date() == TOMORROW and slot.start.astimezone(OSLO).hour == 12
    )


def test_17_a_slot_prices_its_watts_stranded_then_exported_then_imported() -> None:
    """2 kW of surplus, a 1 kW export limit and a 3 kW load: 1 kW at 0, 1 at p_out, 1 at p_in."""
    source = flat_curve()
    slot = _noon_slot(source)
    ctx = plan_ctx(
        source,
        curve_out=export_of(source, "0.30"),
        surplus={slot.start: 2000.0},
        export_limit_w=1000.0,
    )
    p_in = slot.total

    assert ctx.surplus_bands(slot) == ((1000.0, Decimal(0)), (1000.0, Decimal("0.30")))
    assert (
        ctx.effective_price(slot, 3000.0)
        == (Decimal(0) * 1000 + Decimal("0.30") * 1000 + p_in * 1000) / 3000
    )


def test_17_without_an_export_limit_all_the_surplus_costs_the_export() -> None:
    """No limit: the whole surplus is exportable, so every surplus watt costs `p_out`."""
    source = flat_curve()
    slot = _noon_slot(source)
    ctx = plan_ctx(
        source,
        curve_out=export_of(source, "0.30"),
        surplus={slot.start: 2000.0},
    )

    assert ctx.surplus_bands(slot) == ((2000.0, Decimal("0.30")),)


def test_17_the_loads_take_the_surplus_in_priority_order() -> None:
    """The floor (priority 32) takes its share of the sun; the car below it gets the rest."""
    source = flat_curve()
    floor = floor_view(demand=demand(required_kwh=2.0, deadline=None, min_w=0.0, max_w=960.0))
    car = ev_view(
        demand=demand(
            required_kwh=10.0,
            min_w=0.0,
            max_w=7360.0,
            deadline=datetime.combine(TOMORROW, datetime.min.time(), OSLO) + timedelta(hours=16),
        )
    )
    site = plan_all(
        [car, floor],
        curves_of(source),
        site_ctx(forecasts=Sun(MIDDAY)),
        NOW,
        headroom=flat_headroom(source),
    )

    sunny = [
        slot.start
        for slot in source.slots
        if slot.start.astimezone(OSLO).date() == TOMORROW
        and slot.start.astimezone(OSLO).hour in MIDDAY
    ]
    floor_sun = {slot.start: slot.surplus_w for slot in site.plans["loop_bath"].slots}
    car_sun = {slot.start: slot.surplus_w for slot in site.plans["ev"].slots}
    # The sun is free here (no export price): both plan into it, the floor first.
    assert sum(floor_sun.get(start, 0.0) for start in sunny) > 0.0
    for start in sunny:
        taken = floor_sun.get(start, 0.0) + car_sun.get(start, 0.0)
        assert taken <= 3000.0 + 1e-6
    assert any(car_sun.get(start, 0.0) > 0.0 for start in sunny)
    # What the car draws beyond the sun is its grid share, and all it takes of the room.
    for slot in site.plans["ev"].slots:
        assert slot.grid_w is not None
        assert slot.grid_w == pytest.approx(max(0.0, slot.envelope_w - slot.surplus_w))


@pytest.mark.parametrize("seed", range(40))
def test_17_without_a_production_forecast_every_plan_is_the_plan_on_p_in(seed: int) -> None:
    """Property: a forecast with no surplus plans bit for bit as no forecast at all."""
    rng = random.Random(seed)
    source = volatile_curve() if rng.random() < 0.5 else flat_curve()
    loads = [
        ev_view(
            strategy=rng.choice(("deadline_fill", "cheapest_hours")),
            demand=demand(required_kwh=float(rng.randint(2, 40))),
        ),
        floor_view(strategy=rng.choice(("deadline_fill", "heat_capacitor", "best_save"))),
    ]
    headroom = flat_headroom(source, w=float(rng.choice((3000, 6000, 12000))))
    dark = plan_all(
        loads, curves_of(source), site_ctx(forecasts=Weather(surplus=0.0)), NOW, headroom=headroom
    )
    none = plan_all(loads, curves_of(source), site_ctx(), NOW, headroom=headroom)

    assert dark.plans == none.plans
    assert dark.headroom_left == none.headroom_left


def _cost(candidates: list[_Candidate], taken: dict[int, float]) -> Decimal:
    """Return what `taken` costs, each slot's kWh through its bands in order."""
    total = Decimal(0)
    for row in candidates:
        left = taken.get(row.index, 0.0)
        for watts, price in row.bands or ((row.cap_w, row.slot.total),):
            kwh = min(left, watts * row.hours / 1000.0)
            total += price * Decimal(str(kwh))
            left -= kwh
    return total


@pytest.mark.parametrize("seed", range(20))
def test_17_the_greedy_stays_exact_over_surplus_bands(seed: int) -> None:
    """Three one-hour slots with surplus, a stranded band and a priced limit: brute force agrees."""
    rng = random.Random(seed)
    base = flat_curve().slots[0]
    candidates: list[_Candidate] = []
    for index in range(3):
        start = datetime(2026, 9, 24, index, tzinfo=OSLO)
        price = Decimal(rng.randint(10, 60)) / 100
        slot = replace(base, start=start, end=start + timedelta(hours=1), total=price)
        sun_w = float(rng.choice((0, 1000, 2000)))
        stranded = float(rng.choice((0, 500))) if sun_w else 0.0
        sun = tuple(
            band
            for band in (
                (stranded, Decimal(0)),
                (sun_w - stranded, min(Decimal(rng.randint(0, 40)) / 100, price)),
            )
            if band[0] > 0.0
        )
        tier = (float(rng.choice((1000, 2000, 3000))), Decimal(rng.randint(1, 30)) / 100)
        candidates.append(_candidate(index, slot, 4000.0, sun, tier))
    required = float(rng.randint(2, 20)) / 2
    greedy = _cost(candidates, _fill_priced(candidates, required, min_w=0.0, prefer_late=False))
    steps = [x / 2 for x in range(9)]
    best = min(
        _cost(candidates, dict(enumerate(split)))
        for split in itertools.product(steps, repeat=3)
        if abs(sum(split) - required) < 1e-9
    )
    assert greedy == best


def test_17_a_gas_load_plans_without_the_sun() -> None:
    """PV surplus is electricity: a load on another carrier sees no bands."""
    source = flat_curve()
    gas = replace(source, carrier=Carrier.GAS)
    curves = Curves(import_={Carrier.ELECTRICITY: source, Carrier.GAS: gas})
    boiler = floor_view(load_id="boiler", carrier=Carrier.GAS, strategy="deadline_fill")
    site = plan_all(
        [boiler], curves, site_ctx(forecasts=Sun(MIDDAY)), NOW, headroom=flat_headroom(source)
    )

    assert all(slot.surplus_w == 0.0 for slot in site.plans["boiler"].slots)
