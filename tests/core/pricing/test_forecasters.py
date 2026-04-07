"""D1 §9 item 10 - the forecaster chain, both halves.

With two weeks of history the tail is an `ESTIMATED` weekday profile; with less
it is the `SYNTHESISED` floor; and whichever fills it, the curve covers the
whole horizon (INV-5). With every external price source dead the planner still
has to know that night is cheaper than day.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Confidence
from custom_components.powerplan.core.pricing import build_curve
from custom_components.powerplan.core.pricing.compose import CoverageError
from custom_components.powerplan.core.pricing.forecasters.base import (
    PriceForecaster,
    chain,
    missing_intervals,
)
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.forecasters.same_weekday import SameWeekdayProfile
from custom_components.powerplan.core.pricing.forecasters.synthesised import Synthesised
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    TimeFilter,
    TouPeriod,
    TouSchedule,
)
from tests.builders.curves import (
    ORDINARY,
    OSLO,
    TENSIO_DAY,
    TENSIO_NIGHT,
    context,
    day_bounds,
    raw_days,
    volatile_no3_day,
    weekday_shape,
    with_hole,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from custom_components.powerplan.core.pricing import PriceCurve, Slot

HORIZON = timedelta(hours=48)
DEFAULT_ENERGY = Decimal("0.50")

#: Two weeks of history start here; the tail to forecast starts two weeks later.
FIRST_HISTORY_DAY = ORDINARY  # Thursday 2026-12-03
FORECAST_DAY = date(2026, 12, 17)  # the Thursday two weeks after it
#: The week whose prices are ten times everyone else's - the outlier a median is for.
SPIKE_WEEK = tuple(date(2026, 12, 7) + timedelta(days=offset) for offset in range(7))


def tensio() -> TouSchedule:
    """Return Tensio's day/night grid energy charge (D1 §5.4)."""
    return TouSchedule(
        periods=(TouPeriod(when=TimeFilter(hours=((6 * 60, 22 * 60),)), price=TENSIO_DAY),),
        fallback=TENSIO_NIGHT,
    )


def floor_chain() -> PriceForecaster:
    """Return the configured forecaster chain: carry what is known, then the floor."""
    return chain(CarryKnown(), Synthesised(tou=tensio(), energy_default=DEFAULT_ENERGY))


@pytest.mark.inv("INV-5")
def test_10_synthesised_covers_the_horizon_with_nothing_known() -> None:
    """Every source dead: a full 48 h of SYNTHESISED slots, night cheaper than day."""
    now = day_bounds(ORDINARY)[0] + timedelta(hours=6, minutes=7)
    ctx = context(now)

    curve = build_curve((), (), floor_chain(), ctx, HORIZON, now)

    assert curve.slots
    assert not missing_intervals(curve, now, now + HORIZON)
    assert {slot.confidence for slot in curve.slots} == {Confidence.SYNTHESISED}
    assert {slot.minutes for slot in curve.slots} == {15}
    assert curve.sources == ("synthesised",)
    assert curve.coverage_h(now) == 0.0

    tomorrow = day_bounds(date(2026, 12, 4))[0]
    night = curve.price_at(tomorrow + timedelta(hours=3))
    daytime = curve.price_at(tomorrow + timedelta(hours=10))
    assert night is not None
    assert daytime is not None
    assert night.total < daytime.total
    assert daytime.total - night.total == TENSIO_DAY - TENSIO_NIGHT
    assert night.components[SPOT] == DEFAULT_ENERGY
    assert night.components["grid_energy"] == TENSIO_NIGHT


@pytest.mark.inv("INV-5")
def test_10_synthesised_fills_a_hole_and_keeps_every_known_slot() -> None:
    """A partial day is kept and its hole filled from the known slots' mean (D1 §8)."""
    full = volatile_no3_day()
    raw = with_hole(full, first=40, last=56)  # local 10:00–14:00 missing
    now = full[0].start
    ctx = context(now)

    curve = build_curve(raw, (), floor_chain(), ctx, full[-1].end - now, now)

    known = [slot for slot in curve.slots if slot.confidence is Confidence.KNOWN]
    synthesised = [slot for slot in curve.slots if slot.confidence is Confidence.SYNTHESISED]
    assert len(known) == 80
    assert len(synthesised) == 16
    assert not missing_intervals(curve, now, full[-1].end)
    assert list(curve.slots) == sorted(curve.slots, key=lambda slot: slot.start)
    assert curve.sources == ("nordpool_action", "synthesised")

    expected = sum((slot.value for slot in raw), Decimal(0)) / len(raw)
    assert synthesised[0].components[SPOT] == expected
    assert synthesised[0].components[SPOT] != DEFAULT_ENERGY


@pytest.mark.inv("INV-5")
def test_10_synthesised_prefers_the_history_it_is_given() -> None:
    """The constant energy component is the mean of recent known slots (D1 §5.5)."""
    yesterday = volatile_no3_day(date(2026, 12, 2))
    now = day_bounds(ORDINARY)[0]
    ctx = context(now)
    start = yesterday[0].start
    history = list(
        build_curve(yesterday, (), CarryKnown(), context(start), timedelta(hours=24), start).slots
    )

    curve = build_curve((), (), floor_chain(), ctx, HORIZON, now, history=history)

    expected = sum((slot.components[SPOT] for slot in history), Decimal(0)) / len(history)
    assert curve.slots[0].components[SPOT] == expected


@pytest.mark.inv("INV-5")
def test_10_carry_known_alone_leaves_the_horizon_uncovered() -> None:
    """`carry_known` adds nothing, so a chain without the floor cannot cover 48 h."""
    raw = volatile_no3_day()
    now = raw[0].start
    ctx = context(now)

    exact = build_curve(raw, (), CarryKnown(), ctx, raw[-1].end - now, now)
    assert len(exact.slots) == 96
    assert exact.coverage_h(now) == 24.0

    with pytest.raises(CoverageError):
        build_curve(raw, (), CarryKnown(), ctx, HORIZON, now)


def profile_chain() -> PriceForecaster:
    """Return the full three-step chain D1 §5.5 describes."""
    return chain(
        CarryKnown(),
        SameWeekdayProfile(),
        Synthesised(tou=tensio(), energy_default=DEFAULT_ENERGY),
    )


def history(
    first: date, days: int, *, shape: Callable[[datetime], Decimal] = weekday_shape
) -> tuple[Slot, ...]:
    """Return `days` composed local days of history, every slot `KNOWN`."""
    raw = raw_days(first, days, shape=shape)
    start = raw[0].start
    return build_curve(raw, (), CarryKnown(), context(start), raw[-1].end - start, start).slots


def local_day_mean(slots: Sequence[Slot], day: date) -> Decimal:
    """Return the duration-weighted mean total of the slots of the local day."""
    chosen = [slot for slot in slots if slot.start.astimezone(OSLO).date() == day]
    weighted = sum((slot.total * slot.minutes for slot in chosen), Decimal(0))
    return weighted / sum(slot.minutes for slot in chosen)


def total_at(curve: PriceCurve, when: datetime) -> Decimal:
    """Return the total of the curve's slot containing `when`."""
    slot = curve.price_at(when)
    assert slot is not None
    return slot.total


def history_total_at(slots: Sequence[Slot], when: datetime) -> Decimal:
    """Return the total of the history slot containing `when`."""
    return next(slot.total for slot in slots if slot.start <= when < slot.end)


def shape_difference(totals: Callable[[datetime], Decimal], midnight: datetime) -> Decimal:
    """Return the night-minus-morning difference of a local day's shape.

    A level shift leaves every difference inside the day untouched, so this is
    what identifies *which* day the profile took its shape from.
    """
    return totals(midnight + timedelta(hours=3)) - totals(midnight + timedelta(hours=8))


@pytest.mark.inv("INV-5")
def test_10_two_weeks_of_history_give_an_estimated_weekday_profile() -> None:
    """The tail takes the same weekday's shape at the last known day's level."""
    past = history(FIRST_HISTORY_DAY, 14)
    now = day_bounds(FORECAST_DAY)[0]

    curve = build_curve((), (), profile_chain(), context(now), HORIZON, now, history=past)

    assert not missing_intervals(curve, now, now + HORIZON)
    assert {slot.confidence for slot in curve.slots} == {Confidence.ESTIMATED}
    assert curve.sources == ("same_weekday_profile",)
    assert len(curve.slots) == 192

    # Rescaled: each forecast day's mean is the last known day's mean, exactly.
    target = local_day_mean(past, FORECAST_DAY - timedelta(days=1))
    assert curve.mean(FORECAST_DAY, OSLO) == target
    assert curve.mean(FORECAST_DAY + timedelta(days=1), OSLO) == target

    # And the shape is the same weekday's, a week back - not the last known day's.
    same_weekday = day_bounds(FORECAST_DAY - timedelta(days=7))[0]
    assert shape_difference(lambda when: total_at(curve, when), now) == shape_difference(
        lambda when: history_total_at(past, when), same_weekday
    )


@pytest.mark.inv("INV-5")
def test_10_the_profile_takes_the_median_and_ignores_an_outlier_week() -> None:
    """Three weeks, one of them ten times the price: the median is unmoved."""

    def spiky(local: datetime) -> Decimal:
        base = weekday_shape(local)
        return base * 10 if local.date() in SPIKE_WEEK else base

    past = history(FIRST_HISTORY_DAY - timedelta(days=7), 21, shape=spiky)
    now = day_bounds(FORECAST_DAY)[0]

    curve = build_curve((), (), profile_chain(), context(now), HORIZON, now, history=past)

    ordinary_thursday = day_bounds(FORECAST_DAY - timedelta(days=14))[0]
    assert {slot.confidence for slot in curve.slots} == {Confidence.ESTIMATED}
    assert shape_difference(lambda when: total_at(curve, when), now) == shape_difference(
        lambda when: history_total_at(past, when), ordinary_thursday
    )


@pytest.mark.inv("INV-5")
def test_10_less_than_two_weeks_is_skipped_and_the_floor_fills() -> None:
    """Under two weeks the profile is skipped; `synthesised` still covers it."""
    past = history(date(2026, 12, 12), 5)
    now = day_bounds(FORECAST_DAY)[0]

    curve = build_curve((), (), profile_chain(), context(now), HORIZON, now, history=past)

    assert not missing_intervals(curve, now, now + HORIZON)
    assert {slot.confidence for slot in curve.slots} == {Confidence.SYNTHESISED}
    assert curve.sources == ("synthesised",)


@pytest.mark.inv("INV-5")
def test_10_the_profile_fills_a_hole_and_keeps_the_known_slots() -> None:
    """A hole inside today is estimated; the known slots stay untouched."""
    full = volatile_no3_day()
    raw = with_hole(full, first=40, last=56)  # local 10:00–14:00 missing
    now = full[0].start
    past = history(date(2026, 11, 19), 14)

    curve = build_curve(
        raw, (), profile_chain(), context(now), full[-1].end - now, now, history=past
    )

    counted = {
        confidence: len([slot for slot in curve.slots if slot.confidence is confidence])
        for confidence in Confidence
    }
    assert counted[Confidence.KNOWN] == 80
    assert counted[Confidence.ESTIMATED] == 16
    assert counted[Confidence.SYNTHESISED] == 0
    assert not missing_intervals(curve, now, full[-1].end)
    assert curve.sources == ("nordpool_action", "same_weekday_profile")


@pytest.mark.inv("INV-5")
def test_10_the_chain_covers_the_horizon_whatever_the_history() -> None:
    """Nothing, five days or two weeks: the horizon is covered (INV-5)."""
    now = day_bounds(FORECAST_DAY)[0]

    for past in ((), history(date(2026, 12, 12), 5), history(FIRST_HISTORY_DAY, 14)):
        curve = build_curve((), (), profile_chain(), context(now), HORIZON, now, history=past)
        assert not missing_intervals(curve, now, now + HORIZON), len(past)
        assert curve.coverage_h(now) == 0.0, len(past)


@pytest.mark.inv("INV-5")
def test_10_a_weekday_the_history_never_reached_is_left_to_the_floor() -> None:
    """The profile fills the weekdays it knows; `synthesised` fills the rest."""
    now = day_bounds(FORECAST_DAY)[0]
    # A Thursday and a Wednesday two weeks apart: enough span, four days short
    # of enough weekdays. The forecast covers a Thursday and a Friday.
    thursday_only = (*history(date(2026, 12, 3), 1), *history(date(2026, 12, 16), 1))

    mixed = build_curve((), (), profile_chain(), context(now), HORIZON, now, history=thursday_only)

    counted = {
        confidence: len([slot for slot in mixed.slots if slot.confidence is confidence])
        for confidence in Confidence
    }
    assert counted[Confidence.ESTIMATED] == 96  # the Thursday
    assert counted[Confidence.SYNTHESISED] == 96  # the Friday
    assert mixed.sources == ("same_weekday_profile", "synthesised")
    assert not missing_intervals(mixed, now, now + HORIZON)

    # With neither weekday in the history the profile adds nothing at all.
    other_weekdays = (*history(date(2026, 12, 1), 1), *history(date(2026, 12, 16), 1))
    floor_only = build_curve(
        (), (), profile_chain(), context(now), HORIZON, now, history=other_weekdays
    )
    assert {slot.confidence for slot in floor_only.slots} == {Confidence.SYNTHESISED}
    assert floor_only.sources == ("synthesised",)


def test_10_the_profile_adds_nothing_when_the_prices_already_reach_the_horizon() -> None:
    """A complete day and a horizon inside it: no forecaster invents a slot."""
    raw = volatile_no3_day()
    now = raw[0].start

    curve = build_curve(
        raw,
        (),
        profile_chain(),
        context(now),
        raw[-1].end - now,
        now,
        history=history(date(2026, 11, 19), 14),
    )

    assert len(curve.slots) == 96
    assert {slot.confidence for slot in curve.slots} == {Confidence.KNOWN}
    assert curve.sources == ("nordpool_action",)
