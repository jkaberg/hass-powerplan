"""D1 §9 item 17 - `spread`, `is_flat` and `coverage_h` on both regimes.

The flat regime is the one the Norwegian house actually lives in: under
Norgespris with no time-of-use grid charge every slot costs the same, price
alone cannot order anything and the flatness threshold has to say so (D1 §5.7,
INV-8). The volatile regime is an NO3 spot day.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Carrier, Confidence, Direction
from custom_components.powerplan.core.pricing import (
    HysteresisPolicy,
    PriceCurve,
    RawSlot,
    Slot,
    build_curve,
)
from custom_components.powerplan.core.pricing.forecasters.base import chain
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.forecasters.synthesised import Synthesised
from custom_components.powerplan.core.pricing.modifiers.base import SPOT, PriceModifier
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.pricing.modifiers.levy import Levy
from custom_components.powerplan.core.pricing.modifiers.tou_schedule import (
    TimeFilter,
    TouPeriod,
    TouSchedule,
)
from custom_components.powerplan.core.pricing.modifiers.vat import Vat
from tests.builders.curves import (
    DST_AUTUMN,
    DST_SPRING,
    NORGESPRIS,
    ORDINARY,
    OSLO,
    TENSIO_DAY,
    TENSIO_NIGHT,
    context,
    day_bounds,
    dst_autumn_day,
    dst_spring_day,
    volatile_no3_day,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

ELAVGIFT = Decimal("0.0163")
VAT_RATE = Decimal("0.25")


def tensio() -> TouSchedule:
    """Return Tensio's day/night grid energy charge (D1 §5.4)."""
    return TouSchedule(
        periods=(TouPeriod(when=TimeFilter(hours=((6 * 60, 22 * 60),)), price=TENSIO_DAY),),
        fallback=TENSIO_NIGHT,
    )


def norgespris_chain(*, with_tou: bool) -> tuple[PriceModifier, ...]:
    """Return the Norgespris chain, optionally with the grid energy charge."""
    head: tuple[PriceModifier, ...] = (FixedPrice(price=NORGESPRIS),)
    if with_tou:
        head = (*head, tensio())
    return (*head, Levy(amount=ELAVGIFT), Vat(rate=VAT_RATE))


def composed(raw: Sequence[RawSlot], chain_: Sequence[PriceModifier]) -> PriceCurve:
    """Compose a whole local day with `chain_` and no forecast."""
    start = raw[0].start
    return build_curve(raw, chain_, CarryKnown(), context(start), raw[-1].end - start, start)


@pytest.mark.inv("INV-8")
def test_17_spread_and_is_flat_on_a_flat_norgespris_day() -> None:
    """With the fixed price and no time-of-use charge every slot costs the same."""
    curve = composed(volatile_no3_day(), norgespris_chain(with_tou=False))
    policy = HysteresisPolicy()

    expected = (NORGESPRIS + ELAVGIFT) * (1 + VAT_RATE)
    assert {slot.total for slot in curve.slots} == {expected}
    assert curve.spread(ORDINARY, OSLO) == Decimal(0)
    assert curve.mean(ORDINARY, OSLO) == expected
    assert policy.flat_threshold(curve, ORDINARY, OSLO) == Decimal("0.03") * expected
    assert policy.is_flat(curve, ORDINARY, OSLO)
    assert curve.is_flat(ORDINARY, OSLO, Decimal("0.001"))


@pytest.mark.inv("INV-8")
def test_17_spread_and_is_flat_on_a_volatile_no3_day() -> None:
    """A spot day spreads 1.8125 NOK/kWh and is not flat (D1 §5.7)."""
    curve = composed(volatile_no3_day(), (tensio(), Levy(amount=ELAVGIFT), Vat(rate=VAT_RATE)))
    policy = HysteresisPolicy()

    dearest = max(slot.total for slot in curve.slots)
    cheapest = min(slot.total for slot in curve.slots)
    assert dearest == (Decimal("1.40") + TENSIO_DAY + ELAVGIFT) * (1 + VAT_RATE)
    assert cheapest == (Decimal("-0.05") + TENSIO_DAY + ELAVGIFT) * (1 + VAT_RATE)
    assert curve.spread(ORDINARY, OSLO) == Decimal("1.8125")
    assert not policy.is_flat(curve, ORDINARY, OSLO)

    # Only the grid charge varies under Norgespris: flat in money, not in shape.
    norgespris = composed(volatile_no3_day(), norgespris_chain(with_tou=True))
    assert norgespris.spread(ORDINARY, OSLO) == (TENSIO_DAY - TENSIO_NIGHT) * (1 + VAT_RATE)
    assert not policy.is_flat(norgespris, ORDINARY, OSLO)


def test_17_coverage_h_counts_only_the_known_slots() -> None:
    """Coverage is how far ahead prices are actually known (D1 §5.7)."""
    raw = volatile_no3_day()
    start = raw[0].start
    known = composed(raw, ())

    assert known.coverage_h(start) == 24.0
    assert known.coverage_h(start + timedelta(hours=12)) == 12.0
    assert known.coverage_h(start + timedelta(hours=24)) == 0.0

    with_tail = build_curve(
        raw,
        (),
        chain(CarryKnown(), Synthesised(tou=tensio())),
        context(start),
        timedelta(hours=48),
        start,
    )
    assert len(with_tail.slots) > 96
    assert with_tail.coverage_h(start) == 24.0
    assert {slot.confidence for slot in with_tail.slots[96:]} == {Confidence.SYNTHESISED}


def test_17_price_at_and_slots_between() -> None:
    """`price_at` finds the containing slot; `slots_between` returns the overlap."""
    raw = volatile_no3_day()
    curve = composed(raw, ())
    start = raw[0].start

    assert curve.price_at(start) is curve.slots[0]
    assert curve.price_at(start + timedelta(minutes=14, seconds=59)) is curve.slots[0]
    assert curve.price_at(start + timedelta(minutes=15)) is curve.slots[1]
    assert curve.price_at(start - timedelta(seconds=1)) is None
    assert curve.price_at(raw[-1].end) is None

    window = curve.slots_between(start + timedelta(minutes=10), start + timedelta(minutes=35))
    assert window == curve.slots[:3]


def test_17_resample_is_a_weighted_mean_for_display() -> None:
    """Resampling averages by duration and keeps the least trusted confidence."""
    raw = volatile_no3_day()
    curve = composed(raw, (tensio(), Levy(amount=ELAVGIFT), Vat(rate=VAT_RATE)))

    hourly = curve.resample(60)
    assert len(hourly.slots) == 24
    assert hourly.slots[0].total == curve.slots[0].total
    assert hourly.slots[0].components[SPOT] == curve.slots[0].components[SPOT]
    assert {slot.minutes for slot in hourly.slots} == {60}
    for slot in hourly.slots:
        assert slot.total == sum(slot.components.values())

    start = datetime(2026, 12, 3, tzinfo=UTC)
    mixed = PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=(
            Slot(
                start,
                start + timedelta(minutes=15),
                Decimal("1.00"),
                {SPOT: Decimal("1.00")},
                Confidence.KNOWN,
            ),
            Slot(
                start + timedelta(minutes=15),
                start + timedelta(minutes=30),
                Decimal("2.00"),
                {SPOT: Decimal("2.00")},
                Confidence.SYNTHESISED,
            ),
        ),
        built_at=start,
        sources=("test",),
    )
    blended = mixed.resample(30)
    assert len(blended.slots) == 1
    assert blended.slots[0].total == Decimal("1.5")
    assert blended.slots[0].confidence is Confidence.SYNTHESISED


@pytest.mark.inv("INV-7")
def test_17_statistics_hold_on_both_dst_days() -> None:
    """The 23- and 25-hour local days resample to 23 and 25 hourly slots (INV-7)."""
    autumn = composed(dst_autumn_day(), (tensio(), Levy(amount=ELAVGIFT), Vat(rate=VAT_RATE)))
    spring = composed(dst_spring_day(), (tensio(), Levy(amount=ELAVGIFT), Vat(rate=VAT_RATE)))

    assert autumn.coverage_h(day_bounds(DST_AUTUMN)[0]) == 25.0
    assert spring.coverage_h(day_bounds(DST_SPRING)[0]) == 23.0
    assert len(autumn.resample(60).slots) == 25
    assert len(spring.resample(60).slots) == 23
    assert autumn.spread(DST_AUTUMN, OSLO) == Decimal("1.8125")
    assert spring.spread(DST_SPRING, OSLO) == Decimal("1.8125")


def _linear_between(curve: PriceCurve, a: datetime, b: datetime) -> tuple[Slot, ...]:
    """D1 §4's definition, spelled out: every slot overlapping `[a, b)`."""
    return tuple(slot for slot in curve.slots if slot.end > a and slot.start < b)


def _linear_at(curve: PriceCurve, t: datetime) -> Slot | None:
    for slot in curve.slots:
        if slot.start > t:
            return None
        if t < slot.end:
            return slot
    return None


def _linear_coverage(curve: PriceCurve, from_: datetime) -> float:
    hours = 0.0
    for slot in curve.slots:
        if slot.end <= from_ or slot.confidence is not Confidence.KNOWN:
            continue
        hours += (slot.end - max(slot.start, from_)).total_seconds() / 3600.0
    return hours


def test_the_indexed_lookups_agree_with_the_linear_definition() -> None:
    """Bisection over a curve with a gap and mixed slot lengths (D-0261).

    `price_at`, `slots_between` and `coverage_h` are asked hundreds of times a
    tick; they are indexed, and the index must answer exactly what the walk did.
    """
    start = day_bounds(ORDINARY)[0]
    slots: list[Slot] = []
    cursor = start
    for index in range(40):
        minutes = 60 if index < 8 else 15
        if index == 12:  # a gap in the past
            cursor += timedelta(minutes=30)
        slots.append(
            Slot(
                start=cursor,
                end=cursor + timedelta(minutes=minutes),
                total=Decimal("0.5") + Decimal(index) / 100,
                components={},
                confidence=Confidence.KNOWN if index < 30 else Confidence.ESTIMATED,
            )
        )
        cursor += timedelta(minutes=minutes)
    curve = PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=tuple(slots),
        built_at=start,
        sources=("test",),
    )
    probes = [start + timedelta(minutes=m) for m in range(-30, 20 * 60, 7)]
    for t in probes:
        assert curve.price_at(t) == _linear_at(curve, t)
        assert curve.coverage_h(t) == pytest.approx(_linear_coverage(curve, t))
        for span in (timedelta(minutes=1), timedelta(minutes=45), timedelta(hours=5)):
            assert curve.slots_between(t, t + span) == _linear_between(curve, t, t + span)
    assert curve.slots_between(start, start) == ()
