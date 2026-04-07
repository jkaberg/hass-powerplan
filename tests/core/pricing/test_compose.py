"""D1 §9 items 4, 11 and 12 - composition, staleness and negative prices.

The Norwegian composition is the one under test throughout: spot (or
Norgespris) + Tensio's grid energy charge + elavgift + 25 % VAT, in that order.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Confidence
from custom_components.powerplan.core.pricing import (
    HysteresisPolicy,
    PriceCurve,
    RawSlot,
    Slot,
    build_curve,
    modifiers,
)
from custom_components.powerplan.core.pricing.compose import DEFAULT_MAX_AGE
from custom_components.powerplan.core.pricing.forecasters.carry_known import CarryKnown
from custom_components.powerplan.core.pricing.modifiers.base import SPOT, PriceModifier
from custom_components.powerplan.core.pricing.modifiers.cumulative_tier import Tier
from custom_components.powerplan.core.pricing.modifiers.day_type import DayTypeRate
from custom_components.powerplan.core.pricing.modifiers.export_price import ExportMode
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
    ORDINARY,
    OSLO,
    TENSIO_DAY,
    TENSIO_NIGHT,
    context,
    day_bounds,
    dst_autumn_day,
    dst_spring_day,
    flat_norgespris_day,
    no3_shape,
    raw_day,
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


def norwegian_chain() -> tuple[PriceModifier, ...]:
    """Return the NO modifier chain in its configured order (D1 §5.4)."""
    return (tensio(), Levy(amount=ELAVGIFT), Vat(rate=VAT_RATE))


def compose_day(
    raw: Sequence[RawSlot],
    chain: Sequence[PriceModifier],
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> PriceCurve:
    """Compose one local day of raw slots with `chain` and no forecast."""
    start = now if now is not None else raw[0].start
    return build_curve(
        raw,
        chain,
        CarryKnown(),
        context(start),
        raw[-1].end - start,
        start,
        max_age=max_age,
    )


def deep_negative_day() -> tuple[RawSlot, ...]:
    """Return an NO3 day whose 13:00 hour is deeply negative (INV-51)."""

    def shape(local: datetime) -> Decimal:
        return Decimal("-0.90") if local.hour == 13 else no3_shape(local)

    return raw_day(ORDINARY, shape=shape)


@pytest.mark.inv("INV-4")
def test_04_components_sum_to_total_in_every_slot() -> None:
    """Every slot keeps its breakdown and `total` is its sum (INV-4)."""
    curve = compose_day(volatile_no3_day(), norwegian_chain())

    assert len(curve.slots) == 96
    for slot in curve.slots:
        assert set(slot.components) == {SPOT, "grid_energy", "levy", "vat"}
        assert slot.total == sum(slot.components.values())
        assert slot.components["levy"] == ELAVGIFT
        assert slot.components["vat"] == VAT_RATE * (
            slot.components[SPOT] + slot.components["grid_energy"] + slot.components["levy"]
        )

    assert curve.slots[0].components["grid_energy"] == TENSIO_NIGHT
    daytime = curve.price_at(day_bounds(ORDINARY)[0] + timedelta(hours=8))
    assert daytime is not None
    assert daytime.components["grid_energy"] == TENSIO_DAY


@pytest.mark.inv("INV-4")
def test_04_modifier_order_is_respected() -> None:
    """VAT charged before the levy taxes less than VAT charged after it (INV-4)."""
    raw = volatile_no3_day()
    tou, levy, vat = norwegian_chain()

    vat_last = compose_day(raw, (tou, levy, vat))
    vat_first = compose_day(raw, (tou, vat, levy))

    assert vat_first.slots[0].components["vat"] < vat_last.slots[0].components["vat"]
    assert vat_first.slots[0].total < vat_last.slots[0].total
    assert vat_first.slots[0].components[SPOT] == vat_last.slots[0].components[SPOT]


@pytest.mark.inv("INV-4")
def test_04_each_modifier_changes_only_its_own_component() -> None:
    """A modifier adds or replaces exactly one component and nothing else (INV-4)."""
    raw = volatile_no3_day()
    ctx = context(raw[0].start)
    slot = Slot(
        start=raw[10].start,
        end=raw[10].end,
        total=raw[10].value,
        components={SPOT: raw[10].value},
        confidence=Confidence.KNOWN,
    )

    for modifier in norwegian_chain():
        after = modifier.apply(slot, ctx)
        changed = {
            key
            for key in set(after.components) | set(slot.components)
            if after.components.get(key) != slot.components.get(key)
        }
        assert changed == {modifier.component}
        assert after.start == slot.start
        assert after.end == slot.end
        assert after.confidence is slot.confidence
        slot = after


@pytest.mark.inv("INV-4")
def test_04_two_sources_are_merged_by_priority_then_by_freshness() -> None:
    """The configured source order decides; inside a rank the fresher fetch wins."""
    start = day_bounds(ORDINARY)[0]
    primary = volatile_no3_day(source="nordpool_action")
    secondary = flat_norgespris_day(source="entity")
    stale_primary = volatile_no3_day(
        source="nordpool_action", fetched_at=start - timedelta(hours=3)
    )
    fresh_primary = flat_norgespris_day(
        source="nordpool_action", fetched_at=start - timedelta(hours=1)
    )

    ranked = build_curve(
        [*secondary, *primary],
        (),
        CarryKnown(),
        context(start),
        primary[-1].end - start,
        start,
        source_priority=("nordpool_action", "entity"),
    )
    assert ranked.slots[0].components[SPOT] == primary[0].value
    assert ranked.sources == ("nordpool_action",)

    other_way = build_curve(
        [*secondary, *primary],
        (),
        CarryKnown(),
        context(start),
        primary[-1].end - start,
        start,
        source_priority=("entity", "nordpool_action"),
    )
    assert other_way.slots[0].components[SPOT] == secondary[0].value

    # Same source, two fetches: the later `fetched_at` wins (D1 §2).
    corrected = build_curve(
        [*stale_primary, *fresh_primary],
        (),
        CarryKnown(),
        context(start),
        primary[-1].end - start,
        start,
    )
    assert corrected.slots[0].components[SPOT] == fresh_primary[0].value


@pytest.mark.inv("INV-5", "INV-8")
def test_11_stale_after_max_age_doubles_the_hysteresis_threshold() -> None:
    """Raw slots older than `max_age` are STALE and double the threshold (INV-8)."""
    start = day_bounds(ORDINARY)[0]
    max_age = timedelta(hours=12)
    fresh = compose_day(
        volatile_no3_day(fetched_at=start - timedelta(hours=11)),
        norwegian_chain(),
        max_age=max_age,
    )
    stale = compose_day(
        volatile_no3_day(fetched_at=start - timedelta(hours=13)),
        norwegian_chain(),
        max_age=max_age,
    )

    assert {slot.confidence for slot in fresh.slots} == {Confidence.KNOWN}
    assert {slot.confidence for slot in stale.slots} == {Confidence.STALE}

    policy = HysteresisPolicy()
    expected = Decimal(str(policy.fraction_of_spread)) * fresh.spread(ORDINARY, OSLO)
    assert expected > policy.floor_major
    assert policy.threshold(fresh, ORDINARY, OSLO) == expected
    assert policy.threshold(stale, ORDINARY, OSLO) == 2 * expected


@pytest.mark.inv("INV-8")
def test_11_the_floor_holds_on_a_flat_day() -> None:
    """A flat day falls back to the minor-unit floor, not to zero (INV-8)."""
    curve = compose_day(flat_norgespris_day(), ())
    policy = HysteresisPolicy()

    assert curve.spread(ORDINARY, OSLO) == Decimal(0)
    assert policy.threshold(curve, ORDINARY, OSLO) == policy.floor_major


@pytest.mark.inv("INV-51")
def test_12_negative_prices_pass_through_every_modifier_unclamped() -> None:
    """No registered modifier clamps a negative spot (INV-51).

    Two of the nine write the energy component themselves - `fixed_price` above
    its cap and `export_price` - so for those the assertion is that what they
    write stays below zero rather than that spot is untouched. Every other
    modifier must leave spot exactly as it found it.
    """
    options: dict[str, dict[str, object]] = {
        "vat": {"rate": VAT_RATE},
        "levy": {"amount": ELAVGIFT},
        "spot_scale": {"mult": Decimal("1.05"), "offset": Decimal("0.02")},
        "fixed_price": {"price": Decimal("0.40"), "cap_kwh_per_month": 5000.0},
        "subsidy_threshold": {"threshold": Decimal("0.9125")},
        "tou_schedule": {"fallback": TENSIO_NIGHT},
        "day_type": {"rates": {"cpp": DayTypeRate(multiplier=Decimal("3"))}},
        "cumulative_tier": {"tiers": (Tier(upto_kwh=None, price=Decimal("0.08")),)},
        "export_price": {"mode": ExportMode.SPOT_MINUS, "amount": Decimal("0.05")},
    }
    assert set(options) == set(modifiers.keys()), "a new modifier must be listed here"

    raw = volatile_no3_day()
    # Month-to-date past the Norgespris cap, so `fixed_price` leaves spot alone;
    # and a `cpp` day type, so `day_type` has a multiplier to apply to it.
    ctx = context(raw[0].start, mtd_kwh=6000.0, day_type="cpp")
    negative = Slot(
        start=raw[52].start,
        end=raw[52].end,
        total=Decimal("-0.30"),
        components={SPOT: Decimal("-0.30")},
        confidence=Confidence.KNOWN,
    )

    for key, kwargs in options.items():
        modifier = modifiers.build(key, kwargs)
        after = modifier.apply(negative, ctx)
        if modifier.component == SPOT:
            assert after.components[SPOT] < 0, key
        else:
            assert after.components[SPOT] == Decimal("-0.30"), key
        assert modifier.component in after.components, key
        assert after.total == sum(after.components.values()), key
        assert after.total < 0, key


@pytest.mark.inv("INV-51")
def test_12_a_deeply_negative_hour_composes_to_a_negative_total() -> None:
    """A paid-for hour survives grid charge, levy and VAT with its sign (INV-51)."""
    curve = compose_day(deep_negative_day(), norwegian_chain())
    cheapest = min(curve.slots, key=lambda slot: slot.total)

    assert cheapest.components[SPOT] == Decimal("-0.90")
    assert cheapest.components["vat"] < 0
    assert cheapest.total < 0
    assert cheapest.total == sum(cheapest.components.values())


@pytest.mark.inv("INV-51")
def test_12_a_negative_spot_earns_no_subsidy_and_keeps_its_sign() -> None:
    """`subsidy_threshold` leaves a negative spot alone by default (INV-51)."""
    subsidy = modifiers.build("subsidy_threshold", {"threshold": Decimal("0.9125")})
    raw = volatile_no3_day()
    ctx = context(raw[0].start)
    slot = Slot(
        start=raw[52].start,
        end=raw[52].end,
        total=Decimal("-0.05"),
        components={SPOT: Decimal("-0.05")},
        confidence=Confidence.KNOWN,
    )

    after = subsidy.apply(slot, ctx)
    assert after.components["subsidy"] == Decimal(0)
    assert after.total == Decimal("-0.05")


@pytest.mark.inv("INV-7")
def test_dst_days_carry_92_and_100_quarter_slots() -> None:
    """A local day has 23, 24 or 25 hours; slot length is per slot (INV-7)."""
    autumn_raw = dst_autumn_day()
    spring_raw = dst_spring_day()
    assert len(autumn_raw) == 100
    assert len(spring_raw) == 92

    autumn = compose_day(autumn_raw, norwegian_chain())
    spring = compose_day(spring_raw, norwegian_chain())

    assert len(autumn.slots) == 100
    assert len(spring.slots) == 92
    assert {slot.minutes for slot in autumn.slots} == {15}
    assert {slot.minutes for slot in spring.slots} == {15}

    autumn_start, autumn_end = day_bounds(DST_AUTUMN)
    spring_start, spring_end = day_bounds(DST_SPRING)
    assert autumn_end - autumn_start == timedelta(hours=25)
    assert spring_end - spring_start == timedelta(hours=23)
    assert len(autumn.slots_between(autumn_start, autumn_end)) == 100
    assert len(spring.slots_between(spring_start, spring_end)) == 92

    # The repeated local hour is priced twice, at two different UTC instants.
    repeated = [slot for slot in autumn.slots if slot.start.astimezone(OSLO).hour == 2]
    assert len(repeated) == 8
    assert len({slot.start for slot in repeated}) == 8
