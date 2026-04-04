"""D1 §9 item 10, the `synthesised` half - the floor beneath every source.

With every external price source dead the planner still has to know that night
is cheaper than day, and the curve still has to cover the whole horizon
(INV-5). `same_weekday_profile` and the `ESTIMATED` half of item 10 are WP4.2.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

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
from custom_components.powerplan.core.pricing.forecasters.synthesised import Synthesised
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    TimeFilter,
    TouPeriod,
    TouSchedule,
)
from tests.builders.curves import (
    ORDINARY,
    TENSIO_DAY,
    TENSIO_NIGHT,
    context,
    day_bounds,
    volatile_no3_day,
    with_hole,
)

HORIZON = timedelta(hours=48)
DEFAULT_ENERGY = Decimal("0.50")


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
