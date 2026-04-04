"""D1 §9 items 5 and 6 - the Norgespris cap and strømstøtte.

Item 7 (the Spanish 2.0TD and Danish 3.0 presets and the URDB import) is
WP4.2; the `tou_schedule` test here covers only what the Norwegian
composition needs - a day/night schedule, a wrapping range and the holiday
modes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.model import Confidence
from custom_components.powerplan.core.pricing import Slot, build_curve
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.pricing.modifiers.levy import Levy
from custom_components.powerplan.core.pricing.modifiers.spot_scale import SpotScale
from custom_components.powerplan.core.pricing.modifiers.subsidy_threshold import (
    NegativeRule,
    SubsidyThreshold,
)
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    HolidayMode,
    TimeFilter,
    TouPeriod,
    TouSchedule,
)
from custom_components.powerplan.core.pricing.modifiers.vat import Vat
from tests.builders.curves import (
    NORGESPRIS,
    ORDINARY,
    OSLO,
    TENSIO_DAY,
    TENSIO_NIGHT,
    FixedHolidays,
    context,
    day_bounds,
    volatile_no3_day,
)

#: strømstøtte's threshold, NOK/kWh ex VAT.
THRESHOLD = Decimal("0.9125")
#: Norgespris' monthly cap, kWh.
CAP = 5000.0


def slot_at(when: datetime, spot: Decimal) -> Slot:
    """Return a one-quarter slot carrying `spot` and nothing else."""
    return Slot(
        start=when,
        end=when + timedelta(minutes=15),
        total=spot,
        components={SPOT: spot},
        confidence=Confidence.KNOWN,
    )


def test_05_fixed_price_holds_below_the_cap_and_lets_spot_back_above_it() -> None:
    """Norgespris prices every slot until the projected month-to-date hits the cap."""
    raw = volatile_no3_day()
    noon = day_bounds(ORDINARY)[0] + timedelta(hours=12)
    ctx = context(noon, mtd_kwh=4900.0, mtd_kwh_per_hour=12.5)
    curve = build_curve(
        raw,
        (FixedPrice(price=NORGESPRIS, cap_kwh_per_month=CAP), Vat(rate=Decimal("0.25"))),
        CarryKnown(),
        ctx,
        raw[-1].end - noon,
        noon,
    )

    switch = next(i for i, slot in enumerate(curve.slots) if ctx.mtd_kwh_at(slot.start) >= CAP)
    assert switch == 80  # local 20:00, eight hours of 12.5 kWh/h after noon

    for index, slot in enumerate(curve.slots):
        if index < switch:
            assert slot.components[SPOT] == NORGESPRIS, index
        else:
            assert slot.components[SPOT] == raw[index].value, index
        assert slot.total == sum(slot.components.values())

    # The fixed price is entered ex VAT: 0.40 → 0.50 incl. VAT (D1 §5.4).
    assert curve.slots[0].total == Decimal("0.50")
    # Past slots are priced from the actual month-to-date, not the projection.
    assert ctx.mtd_kwh_at(curve.slots[0].start) < CAP


def test_05_fixed_price_without_a_cap_prices_every_slot() -> None:
    """A fixed contract with no cap replaces spot everywhere (D1 §5.4)."""
    raw = volatile_no3_day()
    ctx = context(raw[0].start, mtd_kwh=99_000.0)
    curve = build_curve(
        raw,
        (FixedPrice(price=NORGESPRIS),),
        CarryKnown(),
        ctx,
        raw[-1].end - raw[0].start,
        raw[0].start,
    )

    assert {slot.components[SPOT] for slot in curve.slots} == {NORGESPRIS}


def test_06_subsidy_threshold_pays_the_share_above_the_threshold() -> None:
    """strømstøtte pays 90 % of what spot exceeds the threshold by (D1 §5.4)."""
    subsidy = SubsidyThreshold(threshold=THRESHOLD)
    ctx = context(day_bounds(ORDINARY)[0])

    expensive = subsidy.apply(slot_at(ctx.now, Decimal("1.50")), ctx)
    assert expensive.components["subsidy"] == -Decimal("0.9") * (Decimal("1.50") - THRESHOLD)
    assert expensive.total == Decimal("1.50") - Decimal("0.9") * (Decimal("1.50") - THRESHOLD)

    cheap = subsidy.apply(slot_at(ctx.now, Decimal("0.50")), ctx)
    assert cheap.components["subsidy"] == Decimal(0)
    assert cheap.total == Decimal("0.50")


@pytest.mark.inv("INV-51")
def test_06_subsidy_threshold_honours_the_negative_spot_rule() -> None:
    """The NO rule settles a negative spot at the threshold; spot keeps its sign."""
    ctx = context(day_bounds(ORDINARY)[0])
    spot = Decimal("-0.20")

    default = SubsidyThreshold(threshold=THRESHOLD).apply(slot_at(ctx.now, spot), ctx)
    assert default.components["subsidy"] == Decimal(0)
    assert default.total == spot

    norwegian = SubsidyThreshold(threshold=THRESHOLD, negative_rule=NegativeRule.THRESHOLD).apply(
        slot_at(ctx.now, spot), ctx
    )
    assert norwegian.components[SPOT] == spot
    assert norwegian.components["subsidy"] == THRESHOLD - spot
    assert norwegian.total == THRESHOLD


def test_tou_schedule_first_match_wins_and_ranges_may_wrap() -> None:
    """A day/night schedule prices by local time; the first matching period wins."""
    start = day_bounds(ORDINARY)[0]
    ctx = context(start)
    day_first = TouSchedule(
        periods=(TouPeriod(when=TimeFilter(hours=((6 * 60, 22 * 60),)), price=TENSIO_DAY),),
        fallback=TENSIO_NIGHT,
    )
    night_first = TouSchedule(
        periods=(
            TouPeriod(when=TimeFilter(hours=((22 * 60, 6 * 60),)), price=TENSIO_NIGHT),
            TouPeriod(when=None, price=TENSIO_DAY),
        ),
        fallback=Decimal("9.99"),
    )

    for hour, expected in (
        (0, TENSIO_NIGHT),
        (6, TENSIO_DAY),
        (21, TENSIO_DAY),
        (22, TENSIO_NIGHT),
    ):
        when = start + timedelta(hours=hour)
        assert day_first.price_at(when, ctx) == expected, hour
        assert night_first.price_at(when, ctx) == expected, hour


def test_tou_schedule_holiday_modes() -> None:
    """`as_sunday` moves a holiday onto Sunday's period; `exclude` skips it."""
    thursday = date(2026, 12, 3)
    start = day_bounds(thursday)[0] + timedelta(hours=10)
    ctx = context(start, holidays=FixedHolidays(thursday))
    weekday_only = TimeFilter(weekdays=(0, 1, 2, 3, 4))
    sunday_only = TimeFilter(weekdays=(6,), holidays=HolidayMode.AS_SUNDAY)
    excluded = TimeFilter(weekdays=(0, 1, 2, 3, 4), holidays=HolidayMode.EXCLUDE)

    assert start.astimezone(OSLO).weekday() == 3
    assert weekday_only.matches(start, ctx.tz, ctx.holidays)
    assert sunday_only.matches(start, ctx.tz, ctx.holidays)
    assert not excluded.matches(start, ctx.tz, ctx.holidays)

    ordinary = context(start)
    assert not sunday_only.matches(start, ordinary.tz, ordinary.holidays)
    assert excluded.matches(start, ordinary.tz, ordinary.holidays)


def test_tou_schedule_months_filter() -> None:
    """A period limited to months only matches inside them (D1 §5.4)."""
    ctx = context(day_bounds(ORDINARY)[0])
    winter = TimeFilter(months=(10, 11, 12, 1, 2, 3))
    summer = TimeFilter(months=(4, 5, 6, 7, 8, 9))

    assert winter.matches(ctx.now, ctx.tz, ctx.holidays)
    assert not summer.matches(ctx.now, ctx.tz, ctx.holidays)


def test_spot_scale_marks_up_the_spot_component() -> None:
    """A supplier markup is a share of spot plus an offset (D1 §5.4)."""
    ctx = context(day_bounds(ORDINARY)[0])
    markup = SpotScale(mult=Decimal("1.05"), offset=Decimal("0.02"))

    after = markup.apply(slot_at(ctx.now, Decimal("1.00")), ctx)
    assert after.components["supplier"] == Decimal("0.07")
    assert after.total == Decimal("1.07")


def test_levy_applies_only_in_the_configured_months() -> None:
    """Elavgift is reduced in January–March: outside its months the levy is 0."""
    ctx = context(day_bounds(ORDINARY)[0])
    reduced = Levy(amount=Decimal("0.0163"), months=(1, 2, 3))
    full = Levy(amount=Decimal("0.1644"), months=(4, 5, 6, 7, 8, 9, 10, 11, 12))

    assert reduced.apply(slot_at(ctx.now, Decimal("1.00")), ctx).components["levy"] == Decimal(0)
    assert full.apply(slot_at(ctx.now, Decimal("1.00")), ctx).components["levy"] == Decimal(
        "0.1644"
    )


def test_levy_can_start_above_a_month_to_date_threshold() -> None:
    """A levy with `applies_above_mtd_kwh` is 0 until the month passes it."""
    start = day_bounds(ORDINARY)[0]
    ctx = context(start, mtd_kwh=900.0, mtd_kwh_per_hour=10.0)
    levy = Levy(amount=Decimal("0.05"), applies_above_mtd_kwh=1000.0)

    assert levy.apply(slot_at(start, Decimal("1.00")), ctx).components["levy"] == Decimal(0)
    later = start + timedelta(hours=20)
    assert levy.apply(slot_at(later, Decimal("1.00")), ctx).components["levy"] == Decimal("0.05")
