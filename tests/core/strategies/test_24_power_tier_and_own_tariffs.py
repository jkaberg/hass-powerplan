"""D5 §9 23–25 - the power tier, a load on its own tariff, a load the grid switches.

23 (O23): a priced limit is a second price tier in every slot - LU's 7 kW
reference power at 0.0765 per kWh above it. 24 (G13): a §14a Modul 3 heat pump
plans on its own curve. 25 (G14): an HDO water heater plans nothing outside its
windows, and says so when they are too short.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.strategies import Curves, Headroom, LoadView, plan_all
from custom_components.powerplan.core.strategies.deadline_fill import _Candidate, _fill_priced
from custom_components.powerplan.core.tariffs import TimeFilter
from tests.builders.curves import OSLO

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import PriceCurve
from tests.core.strategies.conftest import (
    NOW,
    curves_of,
    demand,
    ev_view,
    flat_curve,
    site_ctx,
)

SURCHARGE = Decimal("0.0765")
LIMIT_W = 7000.0


def _tiered(source: PriceCurve, *, tier_w: float = LIMIT_W) -> Headroom:
    """Unconstrained headroom with LU's reference power in every slot."""
    return Headroom(
        by_slot={slot.start: float("inf") for slot in source.slots},
        tier={slot.start: tier_w for slot in source.slots},
        surcharge={slot.start: SURCHARGE for slot in source.slots},
    )


def _ev(required_kwh: float) -> LoadView:
    return ev_view(
        demand=demand(required_kwh=required_kwh, min_w=0.0, max_w=11_000.0),
        nameplate_w=11_000.0,
    )


def _above_kwh(plan_slots: tuple, limit_w: float = LIMIT_W) -> float:  # type: ignore[type-arg]
    """Return the kWh planned above the limit, over every slot."""
    return sum(max(0.0, slot.kwh - limit_w * slot.hours / 1000.0) for slot in plan_slots)


def test_23_on_a_flat_night_it_stays_under_the_limit_while_the_night_allows() -> None:
    """30 kWh by 07:00 fits under 7 kW: nothing crosses."""
    source = flat_curve()
    plan = plan_all([_ev(30.0)], curves_of(source), site_ctx(), NOW, headroom=_tiered(source))
    chosen = plan.plans["ev"]
    assert chosen.planned_kwh == pytest.approx(30.0)
    assert _above_kwh(chosen.slots) == pytest.approx(0.0, abs=1e-6)


def test_23_it_crosses_only_by_what_the_night_cannot_hold_under_the_limit() -> None:
    """Needing more than 7 kW × the night crosses by exactly the excess."""
    source = flat_curve()
    room = _tiered(source)
    ev = _ev(1.0)
    until = ev.demand.deadline
    assert until is not None
    # The slot now running counts whole: the plan's slots are the curve's.
    under = sum(
        LIMIT_W * (slot.end - slot.start).total_seconds() / 3_600_000.0
        for slot in source.slots
        if slot.end > NOW and slot.start < until
    )
    need = under + 5.0
    plan = plan_all([_ev(need)], curves_of(source), site_ctx(), NOW, headroom=room).plans["ev"]
    assert plan.planned_kwh == pytest.approx(need, rel=1e-6)
    assert _above_kwh(plan.slots) == pytest.approx(5.0, rel=1e-3)


@pytest.mark.parametrize(("cheaper", "crosses"), [("0.30", True), ("0.05", False)])
def test_23_a_cheaper_slot_is_crossed_in_exactly_when_it_saves_more_than_the_surcharge(
    cheaper: str, crosses: bool
) -> None:
    """One slot `cheaper` below the rest: 11 kW there only if the saving beats 0.0765."""
    base = flat_curve()
    index = next(i for i, slot in enumerate(base.slots) if slot.start >= NOW + timedelta(hours=2))
    slots = list(base.slots)
    price = slots[index].total - Decimal(cheaper)
    slots[index] = replace(slots[index], total=price, components={"spot": price})
    source = replace(base, slots=tuple(slots))
    plan = plan_all(
        [_ev(20.0)], curves_of(source), site_ctx(), NOW, headroom=_tiered(source)
    ).plans["ev"]
    cheap = next(slot for slot in plan.slots if slot.start == source.slots[index].start)
    assert cheap.kwh == pytest.approx((11_000.0 if crosses else LIMIT_W) * cheap.hours / 1000.0)
    assert (_above_kwh(plan.slots) > 1e-6) is crosses


def _cost(candidates: list[_Candidate], taken: dict[int, float]) -> Decimal:
    total = Decimal(0)
    for row in candidates:
        kwh = taken.get(row.index, 0.0)
        under = min(kwh, (row.tier_w or row.cap_w) * row.hours / 1000.0)
        total += row.price * Decimal(str(kwh)) + row.surcharge * Decimal(str(kwh - under))
    return total


@pytest.mark.parametrize("seed", range(20))
def test_23_the_greedy_equals_brute_force_on_slot_tier_instances(seed: int) -> None:
    """Three one-hour slots, each with a tier; every 0.5 kWh split is tried."""
    rng = random.Random(seed)
    base = flat_curve().slots[0]
    candidates = []
    for index in range(3):
        start = datetime(2026, 9, 24, index, tzinfo=OSLO)
        price = Decimal(rng.randint(10, 60)) / 100
        slot = replace(base, start=start, end=start + timedelta(hours=1), total=price)
        candidates.append(
            _Candidate(
                index=index,
                slot=slot,
                cap_w=4000.0,
                tier_w=float(rng.choice((1000, 2000, 3000))),
                surcharge=Decimal(rng.randint(1, 30)) / 100,
            )
        )
    required = float(rng.randint(2, 20)) / 2
    greedy = _cost(candidates, _fill_priced(candidates, required, min_w=0.0, prefer_late=False))
    steps = [x / 2 for x in range(9)]
    best = min(
        _cost(candidates, dict(enumerate(split)))
        for split in itertools.product(steps, repeat=3)
        if abs(sum(split) - required) < 1e-9
    )
    assert greedy == best


def _shifted(base: PriceCurve, cheap_hours: range) -> PriceCurve:
    """Return `base` with 0.5 added outside `cheap_hours` (local)."""
    extra = Decimal("0.5")
    return replace(
        base,
        slots=tuple(
            slot
            if slot.start.astimezone(OSLO).hour in cheap_hours
            else replace(slot, total=slot.total + extra, components={"spot": slot.total + extra})
            for slot in base.slots
        ),
    )


def _hours(plan_slots: tuple, *, next_day: bool = False) -> set[int]:  # type: ignore[type-arg]
    """Return the local hours planned; `next_day` leaves out tonight, which runs in all of it."""
    day = NOW.astimezone(OSLO).date() + timedelta(days=1)
    return {
        slot.start.astimezone(OSLO).hour
        for slot in plan_slots
        if slot.kwh > 0.0 and (not next_day or slot.start.astimezone(OSLO).date() == day)
    }


def test_24_a_modul_3_heat_pump_plans_on_its_own_curve() -> None:
    """The house is cheapest 12–16, Modul 3's NT 01–05: each load follows its own."""
    base = flat_curve(days=2)
    house = _shifted(base, range(12, 16))
    modul3 = _shifted(base, range(1, 5))
    curves = Curves(import_=curves_of(house).import_, per_load={"modul3": modul3})
    wants = {"strategy": "cheapest_hours", "params": {"hours_per_day": 4}}
    pump = ev_view(load_id="hp", grid_tariff="modul3", **wants)
    ev = ev_view(load_id="ev", **wants)
    plans = plan_all([pump, ev], curves, site_ctx(), NOW).plans
    assert _hours(plans["hp"].slots, next_day=True) == set(range(1, 5))
    assert _hours(plans["ev"].slots, next_day=True) == set(range(12, 16))


def test_24_a_tariff_the_curves_do_not_carry_plans_on_the_house_curve() -> None:
    """A key with no curve (the copy lost it) falls back to the house's, never to nothing."""
    house = _shifted(flat_curve(days=2), range(12, 16))
    view = ev_view(grid_tariff="gone", strategy="cheapest_hours", params={"hours_per_day": 4})
    plan = plan_all([view], curves_of(house), site_ctx(), NOW).plans["ev"]
    assert _hours(plan.slots, next_day=True) == set(range(12, 16))


HDO = (TimeFilter(hours=((0, 3 * 60),)), TimeFilter(hours=((13 * 60, 15 * 60),)))


@pytest.mark.parametrize("strategy", ["deadline_fill", "always"])
def test_25_an_hdo_water_heater_plans_nothing_outside_its_windows(strategy: str) -> None:
    """HDO 00–03 and 13–15: every kWh planned in them, whatever the strategy."""
    view = ev_view(
        load_id="tank",
        strategy=strategy,
        allowed=HDO,
        demand=demand(required_kwh=6.0, min_w=0.0, max_w=3000.0),
        nameplate_w=3000.0,
    )
    plan = plan_all([view], curves_of(flat_curve()), site_ctx(), NOW).plans["tank"]
    assert _hours(plan.slots) <= {0, 1, 2, 13, 14}
    closed = [slot for slot in plan.slots if slot.start.astimezone(OSLO).hour not in {0, 1, 2}]
    assert all(slot.envelope_w == 0.0 for slot in closed if slot.start < view.demand.deadline)  # type: ignore[operator]
    if strategy == "deadline_fill":
        assert plan.covered


def test_25_a_demand_the_windows_cannot_hold_is_reported_at_risk() -> None:
    """3 kW for the three hours before 07:00 is 9 kWh: 12 kWh is not covered."""
    view = ev_view(
        load_id="tank",
        allowed=HDO,
        demand=demand(required_kwh=12.0, min_w=0.0, max_w=3000.0),
        nameplate_w=3000.0,
    )
    plan = plan_all([view], curves_of(flat_curve()), site_ctx(), NOW).plans["tank"]
    assert _hours(plan.slots) <= {0, 1, 2}
    assert not plan.covered
    assert plan.planned_kwh == pytest.approx(9.0)
