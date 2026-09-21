"""D5 §9 21 - a negative export price: the surplus is charged first, nothing curtailed.

Phase 7 (D5 §2, §5.8). When exporting costs money (−0.20), a kWh of surplus a
load takes is worth that much: the band is priced at −0.20 and ranks before every
other kWh, the battery charges there first, and the tank on `deadline_fill`
heats there before the night. PowerPlan never curtails export (HLD non-goal): no
plan slot is anything but a load's own envelope.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from custom_components.powerplan.core.model import Carrier
from custom_components.powerplan.core.strategies import Curves, plan_all
from tests.builders.curves import OSLO
from tests.core.strategies.conftest import (
    NOW,
    TOMORROW,
    Sun,
    battery_view,
    demand,
    export_of,
    flat_curve,
    flat_headroom,
    floor_view,
    site_ctx,
)

if TYPE_CHECKING:
    from datetime import datetime

MIDDAY = {11: 3000.0, 12: 3000.0, 13: 3000.0}
BATTERY = {"threshold": Decimal("0.05"), "round_trip_eff": 0.95 * 0.95}


def _sunny(start: datetime) -> bool:
    local = start.astimezone(OSLO)
    return local.date() == TOMORROW and local.hour in MIDDAY


def test_21_at_a_negative_export_price_the_surplus_is_charged_first() -> None:
    """The battery's cheapest charge is the sun at −0.20; the tank heats there too."""
    source = flat_curve()
    curves = Curves(
        import_={Carrier.ELECTRICITY: source},
        export={Carrier.ELECTRICITY: export_of(source, "-0.20")},
    )
    battery = battery_view(params={**BATTERY, "allow_grid_charge": True})
    tank = floor_view(
        load_id="tank",
        priority=20,
        demand=demand(required_kwh=2.0, deadline=None, min_w=0.0, max_w=2000.0),
    )
    site = plan_all(
        [battery, tank],
        curves,
        site_ctx(forecasts=Sun(MIDDAY)),
        NOW,
        headroom=flat_headroom(source),
    )

    charged = [slot for slot in site.plans["battery"].slots if (slot.envelope_w or 0.0) > 0.0]
    # The battery fills in the sun before any grid-only slot: at 5 kW, 3 kW at
    # −0.20 and 2 kW at 0.40 is 0.04 a kWh, cheaper than the grid's 0.40.
    assert charged
    assert all(_sunny(slot.start) for slot in charged)
    assert all(slot.price == Decimal("0.04") for slot in charged)
    heated = [slot for slot in site.plans["tank"].slots if slot.kwh > 0.0]
    assert heated
    assert all(_sunny(slot.start) for slot in heated)


def test_21_nothing_is_curtailed() -> None:
    """Every slot of every plan is a load's own envelope: no plan ever asks for less export."""
    source = flat_curve()
    curves = Curves(
        import_={Carrier.ELECTRICITY: source},
        export={Carrier.ELECTRICITY: export_of(source, "-0.20")},
    )
    site = plan_all(
        [battery_view(params=BATTERY)],
        curves,
        site_ctx(forecasts=Sun(MIDDAY)),
        NOW,
        headroom=flat_headroom(source),
    )

    for slot in site.plans["battery"].slots:
        assert slot.surplus_w >= 0.0
        assert slot.envelope_w is None or slot.surplus_w <= max(0.0, slot.envelope_w)
