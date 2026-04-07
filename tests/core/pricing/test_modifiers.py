"""D1 §9 items 5, 6, 8 and 9 - Norgespris, strømstøtte, day types and tiers.

Item 7 - the Spanish 2.0TD and Danish 3.0 schedules and the URDB importer -
has its own file; the `tou_schedule` tests here cover what the Norwegian
composition needs: a day/night schedule, a wrapping range and the holiday
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
from custom_components.powerplan.core.pricing.modifiers.cumulative_tier import (
    CumulativeTier,
    Tier,
    TierBasis,
)
from custom_components.powerplan.core.pricing.modifiers.day_type import DayType, DayTypeRate
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

# --------------------------------------------------------------------------- #
# Tempo (FR) - what the *colour* adds, €/kWh: the EDF Tempo HP grid's red and
# white minus its blue. Blue is the ordinary day and adds nothing, which is why
# it is also the fallback (D1 §5.4).
# --------------------------------------------------------------------------- #
TEMPO_BLUE = Decimal("0")
TEMPO_WHITE = Decimal("0.0285")
TEMPO_RED = Decimal("0.5953")

#: A US baseline: usage above the monthly allowance costs this much more, $/kWh.
BASELINE_KWH = 400.0
ABOVE_BASELINE = Decimal("0.08")

#: DK elafgift minus its reduced rate for electric heating above 4 000 kWh/year.
HEATING_KWH = 4000.0
ELAFGIFT_REDUCTION = Decimal("-0.7535")


def tempo() -> DayType:
    """Return the Tempo day-type modifier, falling back to blue (D1 §5.4)."""
    return DayType(
        rates={
            "tempo_blue": DayTypeRate(price=TEMPO_BLUE),
            "tempo_white": DayTypeRate(price=TEMPO_WHITE),
            "tempo_red": DayTypeRate(price=TEMPO_RED),
        },
        fallback="tempo_blue",
    )


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


def test_08_day_type_prices_each_tempo_colour_from_the_events() -> None:
    """The colour announced for the slot's local day decides (D1 §5.4, §5.6)."""
    start = day_bounds(ORDINARY)[0]
    tomorrow = ORDINARY + timedelta(days=1)
    ctx = context(start, day_types={ORDINARY: "tempo_blue", tomorrow: "tempo_red"})
    modifier = tempo()

    today_noon = modifier.apply(slot_at(start + timedelta(hours=12), Decimal("1.00")), ctx)
    red_noon = modifier.apply(slot_at(start + timedelta(hours=36), Decimal("1.00")), ctx)

    assert today_noon.components["day_type"] == TEMPO_BLUE
    assert today_noon.total == Decimal("1.00")
    assert red_noon.components["day_type"] == TEMPO_RED
    assert red_noon.total == Decimal("1.00") + TEMPO_RED
    # The colour is a property of the *local* day, not of the UTC one.
    assert red_noon.start.astimezone(OSLO).date() == tomorrow


def test_08_an_unknown_day_type_falls_back_to_blue() -> None:
    """An unknown colour, and no colour at all, both take the fallback."""
    start = day_bounds(ORDINARY)[0]
    modifier = tempo()

    unknown = modifier.apply(
        slot_at(start, Decimal("1.00")), context(start, day_type="tempo_mauve")
    )
    silent = modifier.apply(slot_at(start, Decimal("1.00")), context(start))
    unconfigured = DayType(rates={}, fallback="tempo_blue").apply(
        slot_at(start, Decimal("1.00")), context(start, day_type="tempo_red")
    )

    assert unknown.components["day_type"] == TEMPO_BLUE
    assert silent.components["day_type"] == TEMPO_BLUE
    assert unconfigured.components["day_type"] == Decimal(0)
    # A rate that names neither a price nor a multiplier costs nothing.
    assert DayTypeRate().amount(Decimal("1.00")) == Decimal(0)


def test_08_a_day_type_may_be_a_multiplier_on_spot() -> None:
    """Critical peak pricing triples the energy: the component is the extra."""
    start = day_bounds(ORDINARY)[0]
    ctx = context(start, day_type="cpp")
    modifier = DayType(rates={"cpp": DayTypeRate(multiplier=Decimal("3"))})

    after = modifier.apply(slot_at(start, Decimal("1.20")), ctx)

    assert after.components["day_type"] == Decimal("2.40")
    assert after.total == Decimal("3.60")


def test_09_cumulative_tier_steps_on_the_projected_month_to_date() -> None:
    """A US baseline: the tier the projection lands in prices the future slot."""
    raw = volatile_no3_day()
    start = raw[0].start
    ctx = context(start, mtd_kwh=350.0, mtd_kwh_per_hour=12.5)
    modifier = CumulativeTier(
        tiers=(
            Tier(upto_kwh=BASELINE_KWH, price=Decimal(0)),
            Tier(upto_kwh=None, price=ABOVE_BASELINE),
        )
    )

    priced = [modifier.apply(slot_at(slot.start, slot.value), ctx) for slot in raw]
    switch = next(i for i, slot in enumerate(raw) if ctx.mtd_kwh_at(slot.start) >= BASELINE_KWH)

    assert switch == 16  # 50 kWh to go at 12.5 kWh/h is four hours of quarters
    for index, slot in enumerate(priced):
        expected = Decimal(0) if index < switch else ABOVE_BASELINE
        assert slot.components["tier"] == expected, index
        assert slot.total == sum(slot.components.values()), index


def test_09_cumulative_tier_can_be_a_reduction_above_a_yearly_threshold() -> None:
    """The Danish reduced tax is a negative tier on the year to date (D1 §5.4)."""
    start = day_bounds(ORDINARY)[0]
    ctx = context(start, mtd_kwh_per_hour=12.5, ytd_kwh=3900.0)
    modifier = CumulativeTier(
        basis=TierBasis.YEAR,
        tiers=(
            Tier(upto_kwh=HEATING_KWH, price=Decimal(0)),
            Tier(upto_kwh=None, price=ELAFGIFT_REDUCTION),
        ),
    )

    before = modifier.apply(slot_at(start + timedelta(hours=4), Decimal("1.00")), ctx)
    after = modifier.apply(slot_at(start + timedelta(hours=9), Decimal("1.00")), ctx)

    assert ctx.ytd_kwh_at(before.start) < HEATING_KWH
    assert ctx.ytd_kwh_at(after.start) > HEATING_KWH
    assert before.components["tier"] == Decimal(0)
    assert after.components["tier"] == ELAFGIFT_REDUCTION
    assert after.total == Decimal("1.00") + ELAFGIFT_REDUCTION

    # Every step bounded and the total past the last of them: nothing is added,
    # rather than the last step being stretched to infinity.
    bounded = CumulativeTier(tiers=(Tier(upto_kwh=100.0, price=Decimal("0.08")),))
    past_it = slot_at(start + timedelta(hours=10), Decimal("1.00"))  # 125 kWh this month
    assert bounded.apply(past_it, ctx).components["tier"] == Decimal(0)
