"""D11 §9 18–20 - surplus in the ledger: a load's sun at the export price (Phase 7).

A slot's self-consumed production is attributed first to the loads whose plan
meant to take surplus, each up to what it planned and what it drew; those kWh
cost the export they replaced (`p_out`), the rest of the load's kWh the import
price. The site figures are measured and never move: attribution only moves
money between loads. A battery that may not import took only the sun whatever
it charged, and its discharge into the evening import is worth the import price.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.accounting import LoadParams, StoreKind
from custom_components.powerplan.core.accounting.ledger import plus
from tests.core.accounting.conftest import (
    ORDINARY,
    closed_slot,
    curve,
    local,
    price_at,
    shadow_ctx,
    site,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.model import PriceCurve
    from tests.core.accounting.conftest import Site

P_OUT = Decimal("0.30")


def _export() -> PriceCurve:
    return curve(ORDINARY, days=1, shape=lambda _hour: P_OUT)


def _at(hour: int) -> datetime:
    return local(2026, 12, 3, hour, 0).astimezone(UTC)


def test_18_the_ev_s_planned_surplus_is_priced_at_the_export_price() -> None:
    """3 kWh self-consumed, the EV planned 2 and drew 2.5: 2 at `p_out`, 0.5 at `p_in`."""
    import_curve = curve(ORDINARY, days=1)
    with_sun = site(import_curve=import_curve, export=_export())
    with_sun.with_load("ev", shadow_ctx())
    without = site(import_curve=import_curve, export=_export())
    without.with_load("ev", shadow_ctx())
    # 5 kWh produced, 2 exported: 3 eaten at home - 2.5 by the EV, 1 by the house,
    # the half kWh the house was short imported.
    slot = closed_slot(_at(12), loads={"ev": 2.5}, import_kwh=0.5, export_kwh=2.0)

    with_sun.close(replace(slot, production_kwh=5.0, sun_claims={"ev": 2.0}))
    without.close(slot)

    p_in = price_at(import_curve, _at(12))
    ev = with_sun.accounting.status().loads["ev"]
    assert ev.cost.amount == Decimal("2.0") * P_OUT + Decimal("0.5") * p_in
    assert without.accounting.status().loads["ev"].cost.amount == Decimal("2.5") * p_in
    # The site is measured: its figures do not move.
    sun_site = with_sun.accounting.status().site
    plain_site = without.accounting.status().site
    assert sun_site.energy_cost == plain_site.energy_cost
    assert sun_site.export_credit == plain_site.export_credit
    assert sun_site.cost == plain_site.cost


def test_18_less_sun_than_planned_is_shared_pro_rata_up_to_each_plan() -> None:
    """1 kWh self-consumed against two loads that planned 2 each: shared by what they drew."""
    import_curve = curve(ORDINARY, days=1)
    under_test = site(import_curve=import_curve, export=_export())
    under_test.with_load("ev", shadow_ctx())
    under_test.with_load("tank", shadow_ctx())
    slot = closed_slot(
        _at(12),
        loads={"ev": 3.0, "tank": 1.0},
        import_kwh=3.0,
        export_kwh=0.0,
    )
    under_test.close(replace(slot, production_kwh=1.0, sun_claims={"ev": 2.0, "tank": 2.0}))

    p_in = price_at(import_curve, _at(12))
    loads = under_test.accounting.status().loads
    assert loads["ev"].cost.amount == Decimal("0.75") * P_OUT + Decimal("2.25") * p_in
    assert loads["tank"].cost.amount == Decimal("0.25") * P_OUT + Decimal("0.75") * p_in


def test_18_without_a_production_reading_the_attribution_is_an_estimate() -> None:
    """No production sensor: the surplus is what the site exported and the loads drew over import."""
    import_curve = curve(ORDINARY, days=1)
    under_test = site(import_curve=import_curve, export=_export())
    under_test.with_load("ev", shadow_ctx())
    slot = closed_slot(_at(12), loads={"ev": 2.5}, import_kwh=0.5, export_kwh=2.0)
    under_test.close(replace(slot, sun_claims={"ev": 2.0}))

    ev = under_test.accounting.status().loads["ev"]
    p_in = price_at(import_curve, _at(12))
    assert ev.cost.amount == Decimal("2.0") * P_OUT + Decimal("0.5") * p_in
    assert under_test.accounting.state().ledger.loads["ev"].estimated_slots == 1


def _battery_day(*, claims: bool) -> tuple[Site, PriceCurve]:
    """Charge 5 kWh from the noon sun; discharge 4.25 kWh into the 18:00 import."""
    import_curve = curve(ORDINARY, days=1)
    under_test = site(import_curve=import_curve, export=_export())
    under_test.with_load(
        "battery",
        shadow_ctx(params=LoadParams(kind=StoreKind.BATTERY, nameplate_w=5000.0), level_now=0.5),
    )
    for hour in range(24):
        battery = {12: 5.0, 18: -4.25}.get(hour, 0.0)
        slot = closed_slot(
            _at(hour),
            loads={"battery": battery},
            import_kwh=0.0 if hour == 12 else 1.0,
            export_kwh=1.0 if hour == 12 else 0.0,
        )
        if hour == 12:
            slot = replace(slot, production_kwh=7.0)
        if claims:
            slot = replace(slot, sun_claims={"battery": math.inf})
        under_test.close(slot)
    return under_test, import_curve


def test_19_a_solar_battery_costs_the_export_it_stored_and_earns_the_evening_import() -> None:
    """`cost = 5 × p_out(noon) − 4.25 × p_in(18:00)`, and `savings = −cost` (idle reference)."""
    under_test, import_curve = _battery_day(claims=True)

    rec = under_test.accounting.state().ledger.loads["battery"]
    expected = Decimal("5.0") * P_OUT - Decimal("4.25") * price_at(import_curve, _at(18))
    assert rec.cost.amount == expected
    assert rec.savings.amount == -expected


def test_19_a_battery_discharging_while_the_site_exports_sells_at_the_export_price() -> None:
    """A discharge in a slot the site exports net earns `p_out`, not `p_in`."""
    import_curve = curve(ORDINARY, days=1)
    under_test = site(import_curve=import_curve, export=_export())
    under_test.with_load(
        "battery",
        shadow_ctx(params=LoadParams(kind=StoreKind.BATTERY, nameplate_w=5000.0), level_now=0.5),
    )
    under_test.close(closed_slot(_at(14), loads={"battery": -2.0}, import_kwh=0.0, export_kwh=3.0))

    assert (
        under_test.accounting.state().ledger.loads["battery"].cost.amount == Decimal("-2.0") * P_OUT
    )


@pytest.mark.parametrize("claims", [True, False])
def test_20_the_site_identity_holds_with_panels_an_export_curve_and_a_battery(
    claims: bool,
) -> None:
    """`site.savings == Σ load energy savings + capacity savings`, sun attributed or not."""
    under_test, _ = _battery_day(claims=claims)

    status = under_test.accounting.status()
    loads = status.loads["battery"].savings
    assert status.site.energy_savings == loads
    assert status.site.savings == plus(loads, status.site.capacity_savings)
