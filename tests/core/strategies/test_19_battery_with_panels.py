"""D5 §9 19 - the battery with panels: bank the sun for the evening, or sell it (D5 §5.8).

Phase 7. A charge from the sun costs what its export would have earned (0.30);
a discharge into an evening the house imports in is worth the import price.
When `2.00 × η_rt − 0.30` clears the threshold the battery banks noon's surplus
for the evening; at an export price of 0.38 against 0.40 it sells instead. It
never charges from the grid without `allow_grid_charge`, `peak_shave`'s reserve
still holds on a dark day, and above `surplus_priority_soc` the sun goes on to
the loads below it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.model import Carrier
from custom_components.powerplan.core.strategies import Curves, Headroom, plan_all
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    TOMORROW,
    Sun,
    at_hours,
    battery_view,
    demand,
    ev_view,
    export_of,
    flat_curve,
    flat_headroom,
    site_ctx,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import Plan, PriceCurve

MIDDAY = {11: 3000.0, 12: 3000.0, 13: 3000.0}
EVENING = {18: "2.00", 19: "2.00", 20: "2.00"}
BATTERY = {"threshold": Decimal("0.05"), "round_trip_eff": 0.95 * 0.95}


def _curves(source: PriceCurve, export: str) -> Curves:
    return Curves(
        import_={Carrier.ELECTRICITY: source},
        export={Carrier.ELECTRICITY: export_of(source, export)},
    )


def _hour(slot_start: datetime) -> tuple[bool, int]:
    local = slot_start.astimezone(OSLO)
    return local.date() == TOMORROW, local.hour


def _battery(
    *,
    source: PriceCurve,
    export: str = "0.30",
    sun: dict[int, float] | None = None,
    headroom: Headroom | None = None,
    strategy: str = "arbitrage",
    loads: tuple[Any, ...] = (),
    **params: Any,
) -> dict[str, Plan]:
    view = battery_view(strategy=strategy, params={**BATTERY, **params})
    site = plan_all(
        [view, *loads],
        _curves(source, export),
        site_ctx(forecasts=Sun(MIDDAY if sun is None else sun)),
        NOW,
        headroom=flat_headroom(source) if headroom is None else headroom,
    )
    return dict(site.plans)


def _charges(plan: Plan) -> list[datetime]:
    return [slot.start for slot in plan.slots if (slot.envelope_w or 0.0) > 0.0]


def _discharges(plan: Plan) -> list[datetime]:
    return [slot.start for slot in plan.slots if (slot.envelope_w or 0.0) < 0.0]


def test_19_the_sun_is_banked_for_an_evening_worth_more_than_its_export() -> None:
    """2.00 × 0.9025 − 0.30 clears 0.05: charge in the sun, discharge in the evening."""
    plans = _battery(source=at_hours(flat_curve(), EVENING), allow_grid_charge=False)
    battery = plans["battery"]

    charged = _charges(battery)
    assert charged
    assert all(_hour(start) in {(True, hour) for hour in MIDDAY} for start in charged)
    assert any(
        _hour(start) in {(True, hour) for hour in (18, 19, 20)} for start in _discharges(battery)
    )
    # The charge is surplus, not import: nothing of it is the room's.
    assert all(slot.grid_w == 0.0 for slot in battery.slots if slot.start in charged)


def test_19_the_sun_is_sold_when_the_evening_is_not_worth_more() -> None:
    """0.40 × 0.9025 − 0.38 is below 0.05: no trade, the surplus is exported."""
    plans = _battery(source=flat_curve(), export="0.38", allow_grid_charge=False)

    assert _charges(plans["battery"]) == []


def test_19_no_grid_charge_without_allow_grid_charge() -> None:
    """A dark day and a dear evening: grid charging off plans no charge; on, it does."""
    source = at_hours(flat_curve(), EVENING)
    off = _battery(source=source, sun={}, allow_grid_charge=False)["battery"]
    on = _battery(source=source, sun={}, allow_grid_charge=True)["battery"]

    assert _charges(off) == []
    assert _charges(on)


def test_19_peak_shave_still_holds_its_reserve_on_a_dark_day() -> None:
    """No sun: a slot the ceiling is short in is discharged, and charged for before."""
    source = flat_curve()
    short = next(slot.start for slot in source.slots if _hour(slot.start) == (True, 18))
    room = Headroom(
        by_slot={
            slot.start: (-2000.0 if slot.start == short else 10_000.0) for slot in source.slots
        }
    )
    battery = _battery(source=source, sun={}, headroom=room, strategy="peak_shave")["battery"]

    shaved = next(slot for slot in battery.slots if slot.start == short)
    assert shaved.envelope_w == pytest.approx(-2000.0)
    assert any(start < short for start in _charges(battery))


def test_19_above_surplus_priority_soc_the_sun_goes_to_the_loads_below() -> None:
    """At 50 % with `surplus_priority_soc` 50 the battery leaves the sun to the car."""
    source = at_hours(flat_curve(), EVENING)
    by_four = datetime.combine(TOMORROW, datetime.min.time(), OSLO) + timedelta(hours=16)
    car = ev_view(demand=demand(required_kwh=12.0, deadline=by_four, min_w=0.0, max_w=7360.0))

    def car_sun(**params: Any) -> float:
        plans = _battery(
            source=source,
            loads=(car,),
            allow_grid_charge=False,
            **params,
        )
        return sum(slot.surplus_w * slot.hours / 1000.0 for slot in plans["ev"].slots)

    greedy_battery = car_sun()
    polite = _battery(source=source, loads=(car,), allow_grid_charge=False, surplus_priority_soc=50)

    assert _charges(polite["battery"]) == []
    assert car_sun(surplus_priority_soc=50) == pytest.approx(9.0)
    assert greedy_battery < 9.0
