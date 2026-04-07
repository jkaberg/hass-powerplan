"""Normalisation round-trips whatever a source published (D9 §3, D1 §5.2).

A price source publishes in MWh or kWh, in major or minor units, in its own
timezone, at whatever resolution it likes. What comes out is major units per
kWh, UTC, and slots whose length is the length they really have (INV-7) - and
nothing on the way clamps a negative value (INV-51).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from custom_components.powerplan.core.pricing.normalise import (
    ACCEPTED_DAY_HOURS,
    CurrencyMismatchError,
    DayLengthError,
    EnergyUnit,
    Magnitude,
    check_day_length,
    day_hours,
    gaps,
    raw_slots,
    to_major_per_kwh,
    to_utc,
    unit_factor,
)

ZONES = ("UTC", "Europe/Oslo", "America/Phoenix", "Asia/Kolkata", "Australia/Sydney")
VALUES = st.decimals(
    min_value=Decimal("-500"),
    max_value=Decimal("5000"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)
STARTS = st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2028, 1, 1))


@given(
    value=VALUES,
    energy=st.sampled_from(list(EnergyUnit)),
    magnitude=st.sampled_from(list(Magnitude)),
)
def test_unit_and_magnitude_normalisation_is_reversible(
    value: Decimal, energy: EnergyUnit, magnitude: Magnitude
) -> None:
    """Dividing by the same factor gives the published number back exactly."""
    normalised = to_major_per_kwh(
        value, energy, magnitude, source_currency="NOK", site_currency="NOK"
    )

    assert normalised / unit_factor(energy, magnitude) == value
    assert unit_factor(energy, magnitude) > 0


@pytest.mark.inv("INV-51")
@given(
    value=VALUES,
    energy=st.sampled_from(list(EnergyUnit)),
    magnitude=st.sampled_from(list(Magnitude)),
)
def test_normalisation_never_changes_the_sign(
    value: Decimal, energy: EnergyUnit, magnitude: Magnitude
) -> None:
    """A negative published price stays negative (INV-51)."""
    normalised = to_major_per_kwh(
        value, energy, magnitude, source_currency="NOK", site_currency="NOK"
    )

    assert (normalised < 0) == (value < 0)
    assert (normalised == 0) == (value == 0)


@pytest.mark.inv("INV-7")
@settings(deadline=None)
@given(
    zone=st.sampled_from(ZONES),
    first=STARTS,
    minutes=st.sampled_from((15, 30, 60)),
    values=st.lists(VALUES, min_size=1, max_size=120),
)
def test_points_round_trip_through_raw_slots(
    zone: str, first: datetime, minutes: int, values: list[Decimal]
) -> None:
    """(start, value) points come back as slots with the same starts and values."""
    tz = ZoneInfo(zone)
    start = first.replace(tzinfo=UTC)
    step = timedelta(minutes=minutes)
    expected = [(start + index * step, value) for index, value in enumerate(values)]
    points = [(when.astimezone(tz), value) for when, value in expected]

    slots = raw_slots(
        points,
        currency="NOK",
        source="test",
        fetched_at=start,
        source_tz=tz,
        resolution=step,
    )

    assert [(slot.start, slot.value) for slot in slots] == expected
    assert {slot.end - slot.start for slot in slots} == {step}
    assert not gaps(slots)
    assert {slot.currency for slot in slots} == {"NOK"}


@pytest.mark.inv("INV-7")
@settings(deadline=None)
@given(
    zone=st.sampled_from(ZONES),
    first=STARTS,
    minutes=st.sampled_from((15, 30, 60)),
    values=st.lists(VALUES, min_size=2, max_size=60),
)
def test_slot_length_comes_from_the_next_start(
    zone: str, first: datetime, minutes: int, values: list[Decimal]
) -> None:
    """With no declared resolution the next start sets the length (INV-7)."""
    tz = ZoneInfo(zone)
    start = first.replace(tzinfo=UTC)
    step = timedelta(minutes=minutes)
    points = [(start + index * step, value) for index, value in enumerate(values)]

    slots = raw_slots(points, currency="NOK", source="test", fetched_at=start, source_tz=tz)

    assert all(a.end == b.start for a, b in pairwise(slots))
    assert slots[-1].end - slots[-1].start == step  # the last takes the spacing before it
    assert not gaps(slots)


def test_a_hole_is_stored_and_reported() -> None:
    """A partial day keeps what came and names the gap (D1 §5.2, §8)."""
    tz = ZoneInfo("Europe/Oslo")
    start = datetime(2026, 12, 2, 23, tzinfo=UTC)
    step = timedelta(hours=1)
    points = [
        (start + index * step, Decimal("0.50"))
        for index in range(24)
        if index not in {10, 11}  # local 10:00 and 11:00 never arrived
    ]

    slots = raw_slots(
        points, currency="NOK", source="test", fetched_at=start, source_tz=tz, resolution=step
    )

    assert len(slots) == 22
    assert gaps(slots) == ((start + 10 * step, start + 12 * step),)
    assert day_hours(slots, date(2026, 12, 3), tz) == 22.0


def test_a_single_point_with_no_declared_resolution_lasts_an_hour() -> None:
    """One point and no resolution: an hour, the day-ahead default (D1 §5.2)."""
    tz = ZoneInfo("Europe/Oslo")
    start = datetime(2026, 12, 2, 23, tzinfo=UTC)

    slots = raw_slots(
        [(start, Decimal("0.50"))],
        currency="NOK",
        source="test",
        fetched_at=start,
        source_tz=tz,
    )

    assert len(slots) == 1
    assert slots[0].end - slots[0].start == timedelta(hours=1)


def test_a_naive_timestamp_is_localised_with_the_sources_zone() -> None:
    """A source that publishes naive local time is localised, then converted."""
    oslo = ZoneInfo("Europe/Oslo")

    assert to_utc(datetime(2026, 12, 3, 14), oslo) == datetime(2026, 12, 3, 13, tzinfo=UTC)
    assert to_utc(datetime(2026, 7, 3, 14), oslo) == datetime(2026, 7, 3, 12, tzinfo=UTC)
    # The repeated autumn hour is ambiguous in naive local time: the earlier of
    # the two instants is taken (fold 0), and the slot after it is unambiguous.
    assert to_utc(datetime(2026, 10, 25, 2, 30), oslo) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    # An aware timestamp is only converted.
    aware = datetime(2026, 12, 3, 14, tzinfo=oslo)
    assert to_utc(aware, ZoneInfo("America/Phoenix")) == datetime(2026, 12, 3, 13, tzinfo=UTC)


def test_the_local_day_may_be_23_24_or_25_hours_long() -> None:
    """DST days are accepted at 23 and 25 hours and refused at 22 and 26 (INV-7)."""
    assert ACCEPTED_DAY_HOURS == (23, 24, 25)
    for hours in ACCEPTED_DAY_HOURS:
        check_day_length(float(hours), source="test")

    with pytest.raises(DayLengthError):
        check_day_length(22.0, source="test")
    with pytest.raises(DayLengthError):
        check_day_length(26.0, source="test")


def test_13_a_currency_mismatch_is_refused_without_a_rate_and_converted_with_one() -> None:
    """Silently converting prices is worse than refusing them (D1 §5.2).

    What is left of item 13 is the provider path and the repair issue the flow
    raises from this exception (D1 §8) - `providers/prices/`, WP1.2.
    """
    with pytest.raises(CurrencyMismatchError):
        to_major_per_kwh(
            Decimal("100"),
            EnergyUnit.MWH,
            Magnitude.MAJOR,
            source_currency="EUR",
            site_currency="NOK",
        )

    converted = to_major_per_kwh(
        Decimal("100"),
        EnergyUnit.MWH,
        Magnitude.MAJOR,
        source_currency="EUR",
        site_currency="NOK",
        fx_rate=Decimal("11.5"),
    )
    assert converted == Decimal("1.15")


@pytest.mark.inv("INV-51")
def test_13_a_whole_foreign_day_converts_at_the_fixed_rate_or_not_at_all() -> None:
    """A EUR/MWh day into a NOK site: refused without a rate, converted with one.

    EPEX publishes in EUR/MWh and goes negative regularly (HLD §8); a Norwegian
    site reading a German sensor is the case `fx_rate` exists for. The sign
    survives the conversion - nothing clamps it (INV-51).
    """
    tz = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 12, 2, 23, tzinfo=UTC)
    published = [Decimal("83.55"), Decimal("-12.40"), Decimal("0")]
    rate = Decimal("11.5")

    for value in published:
        with pytest.raises(CurrencyMismatchError):
            to_major_per_kwh(
                value,
                EnergyUnit.MWH,
                Magnitude.MAJOR,
                source_currency="EUR",
                site_currency="NOK",
            )

    converted = [
        to_major_per_kwh(
            value,
            EnergyUnit.MWH,
            Magnitude.MAJOR,
            source_currency="EUR",
            site_currency="NOK",
            fx_rate=rate,
        )
        for value in published
    ]
    slots = raw_slots(
        [(start + index * timedelta(hours=1), value) for index, value in enumerate(converted)],
        currency="NOK",
        source="entity",
        fetched_at=start,
        source_tz=tz,
        resolution=timedelta(hours=1),
    )

    assert [slot.value for slot in slots] == [
        Decimal("0.960825"),
        Decimal("-0.14260"),
        Decimal("0"),
    ]
    assert {slot.currency for slot in slots} == {"NOK"}
    assert slots[1].value < 0
