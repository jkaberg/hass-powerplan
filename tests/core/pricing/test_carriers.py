"""D1 §9 items 14 and 15 - one curve per (carrier, direction).

Export is not import with a minus sign: the grid charge, the levy and the VAT a
household pays on what it takes out are not refunded on what it puts back, so
the export curve is composed from the same raw spot with its *own* modifier
chain (D1 §5.4). And a carrier is not a resolution: gas is priced by the day
while electricity is priced by the quarter, and both curves live side by side
(INV-7).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import Carrier, Confidence, Direction
from custom_components.powerplan.core.pricing import PriceCurve, build_curve
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.modifiers.base import GRID_ENERGY, SPOT
from custom_components.powerplan.core.pricing.modifiers.export_price import ExportMode, ExportPrice
from custom_components.powerplan.core.pricing.modifiers.levy import Levy
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    TimeFilter,
    TouPeriod,
    TouSchedule,
)
from custom_components.powerplan.core.pricing.modifiers.vat import Vat
from tests.builders.curves import (
    NO3_SHAPE,
    ORDINARY,
    OSLO,
    TENSIO_DAY,
    TENSIO_NIGHT,
    context,
    day_bounds,
    gas_daily_days,
    raw_days,
    volatile_no3_day,
)

ELAVGIFT = Decimal("0.0163")
VAT_RATE = Decimal("0.25")
#: What the supplier keeps of an exported kWh, NOK/kWh - spot minus 5 øre.
EXPORT_DEDUCTION = Decimal("0.05")
#: A daily gas price, NOK/kWh ex VAT (NL/DE dynamic gas contracts, D1 §2).
GAS_PRICE = Decimal("0.9450")


def norwegian_chain() -> tuple[TouSchedule, Levy, Vat]:
    """Return the import chain: grid energy charge, elavgift, VAT (D1 §5.4)."""
    return (
        TouSchedule(
            periods=(TouPeriod(when=TimeFilter(hours=((6 * 60, 22 * 60),)), price=TENSIO_DAY),),
            fallback=TENSIO_NIGHT,
        ),
        Levy(amount=ELAVGIFT),
        Vat(rate=VAT_RATE),
    )


@pytest.mark.inv("INV-51")
def test_14_the_export_curve_is_spot_minus_without_the_import_modifiers() -> None:
    """The same raw spot gives an import curve and an export curve (D1 §5.4)."""
    raw = volatile_no3_day()
    now = raw[0].start
    horizon = raw[-1].end - now
    ctx = context(now)

    imported = build_curve(raw, norwegian_chain(), CarryKnown(), ctx, horizon, now)
    exported = build_curve(
        raw,
        (ExportPrice(mode=ExportMode.SPOT_MINUS, amount=EXPORT_DEDUCTION),),
        CarryKnown(),
        ctx,
        horizon,
        now,
        direction=Direction.EXPORT,
    )

    assert imported.direction is Direction.IMPORT
    assert exported.direction is Direction.EXPORT
    assert exported.carrier is Carrier.ELECTRICITY
    assert len(exported.slots) == len(imported.slots) == 96

    for index, slot in enumerate(exported.slots):
        assert set(slot.components) == {SPOT}, index
        assert slot.components[SPOT] == raw[index].value - EXPORT_DEDUCTION, index
        assert slot.total == slot.components[SPOT], index
        assert slot.confidence is Confidence.KNOWN, index

    # Nothing the household pays on import is refunded on export.
    assert set(imported.slots[0].components) == {SPOT, GRID_ENERGY, "levy", "vat"}
    assert imported.slots[0].total > exported.slots[0].total

    # The negative hour stays negative and gets more negative, not clamped.
    negative = min(exported.slots, key=lambda slot: slot.total)
    assert negative.total == Decimal(NO3_SHAPE[13]) - EXPORT_DEDUCTION
    assert negative.total < Decimal(NO3_SHAPE[13]) < 0


@pytest.mark.inv("INV-51")
def test_14_the_other_export_modes_price_the_same_raw_slot() -> None:
    """`fixed`, `spot_times` and `from_source` all write the `spot` component."""
    raw = volatile_no3_day()
    now = raw[0].start
    ctx = context(now)
    negative = Decimal(NO3_SHAPE[13])

    def export(modifier: ExportPrice) -> PriceCurve:
        return build_curve(
            raw,
            (modifier,),
            CarryKnown(),
            ctx,
            raw[-1].end - now,
            now,
            direction=Direction.EXPORT,
        )

    fixed = export(ExportPrice(mode=ExportMode.FIXED, amount=Decimal("0.30")))
    share = export(ExportPrice(mode=ExportMode.SPOT_TIMES, share=Decimal("0.9")))
    source = export(ExportPrice(mode=ExportMode.FROM_SOURCE))

    hour_13 = day_bounds(ORDINARY)[0] + timedelta(hours=13)
    assert {slot.total for slot in fixed.slots} == {Decimal("0.30")}
    for curve, expected in (
        (share, negative * Decimal("0.9")),
        (source, negative),
    ):
        slot = curve.price_at(hour_13)
        assert slot is not None
        assert slot.components[SPOT] == expected
        assert slot.total < 0


def test_15_a_gas_carrier_with_daily_slots_coexists_with_electricity_quarters() -> None:
    """Gas is priced by the local day, electricity by the quarter (D1 §2, INV-7)."""
    start = day_bounds(ORDINARY)[0]
    horizon = timedelta(hours=48)
    ctx = context(start)

    electricity = build_curve(
        raw_days(ORDINARY, 2, shape=lambda local: Decimal(NO3_SHAPE[local.hour])),
        norwegian_chain(),
        CarryKnown(),
        ctx,
        horizon,
        start,
    )
    gas = build_curve(
        gas_daily_days(ORDINARY, 3, shape=lambda day: GAS_PRICE + Decimal(day.day) / 100),
        (Vat(rate=VAT_RATE),),
        CarryKnown(),
        ctx,
        horizon,
        start,
        carrier=Carrier.GAS,
    )

    curves = {(curve.carrier, curve.direction): curve for curve in (electricity, gas)}
    assert set(curves) == {
        (Carrier.ELECTRICITY, Direction.IMPORT),
        (Carrier.GAS, Direction.IMPORT),
    }

    assert {slot.minutes for slot in electricity.slots} == {15}
    assert {slot.minutes for slot in gas.slots} == {24 * 60}
    assert len(electricity.slots) == 192
    assert len(gas.slots) == 3

    # One instant, two slot lengths - which is the whole point of INV-7.
    when = start + timedelta(hours=30)
    quarter = electricity.price_at(when)
    daily = gas.price_at(when)
    assert quarter is not None
    assert daily is not None
    assert quarter.minutes == 15
    assert daily.minutes == 24 * 60
    assert daily.start == day_bounds(ORDINARY + timedelta(days=1))[0]
    assert daily.components[SPOT] == GAS_PRICE + Decimal(4) / 100
    assert daily.total == daily.components[SPOT] * (1 + VAT_RATE)

    # Each curve covers the horizon on its own terms.
    assert electricity.coverage_h(start) == 48.0
    assert gas.coverage_h(start) == 72.0
    assert gas.spread(date(2026, 12, 4), OSLO) == Decimal(0)
