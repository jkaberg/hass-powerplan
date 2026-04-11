"""`manual`: a flat or daily price the user typed (D1 §3, §6 "Other carriers").

The source that exists because not every carrier has an integration. Gas, oil,
district heat and "my fixed contract" are a number the household knows and no API
publishes, and a planner that can only read a spot market cannot compare heating a
tank with electricity against heating it with gas (D6's zone cost comparison).

One slot per local day, which is what D1 §2 means by "gas slots are daily": the
day is 23, 24 or 25 hours long and the slot is exactly as long as the day (INV-7).
A date in `daily` overrides the flat price for that day and nothing else.
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.pricing import Carrier, Direction
from custom_components.powerplan.core.pricing.normalise import Magnitude
from custom_components.powerplan.providers.prices import SourceDataError
from custom_components.powerplan.providers.prices.manual import ManualSource

OSLO = ZoneInfo("Europe/Oslo")

DAY = date_type(2026, 9, 19)
FALL_BACK = date_type(2026, 10, 25)
SPRING_FORWARD = date_type(2026, 3, 29)


def source(**options: object) -> ManualSource:
    """Build a manual gas source at 85 øre per kWh."""
    return ManualSource(
        price=Decimal("0.85"),
        currency="NOK",
        site_currency="NOK",
        tz=OSLO,
        carrier=Carrier.GAS,
        **options,  # type: ignore[arg-type]
    )


async def test_a_flat_price_is_one_slot_per_local_day() -> None:
    """D1 §2: electricity slots are 15/60 min, gas slots are daily."""
    slots = await source().fetch(DAY)

    assert len(slots) == 1
    assert slots[0].start.isoformat() == "2026-09-18T22:00:00+00:00"
    assert slots[0].end.isoformat() == "2026-09-19T22:00:00+00:00"
    assert slots[0].value == Decimal("0.85")
    assert slots[0].currency == "NOK"
    assert slots[0].source == "manual"


@pytest.mark.inv("INV-7")
@pytest.mark.parametrize(
    ("day", "hours"),
    [(DAY, 24), (SPRING_FORWARD, 23), (FALL_BACK, 25)],
    ids=["ordinary", "spring_forward", "fall_back"],
)
async def test_a_daily_slot_is_as_long_as_the_local_day(day: date_type, hours: int) -> None:
    """A daily slot on a DST day is 23 or 25 hours, not 24 (INV-7, D1 §5.8)."""
    slots = await source().fetch(day)

    assert (slots[0].end - slots[0].start).total_seconds() == hours * 3600


async def test_a_typed_price_for_one_day_overrides_the_flat_one() -> None:
    """A daily price typed by the user is what an oil delivery looks like (D1 §3)."""
    typed = source(daily={DAY: Decimal("1.24")})

    today = await typed.fetch(DAY)
    tomorrow = await typed.fetch(date_type(2026, 9, 20))

    assert today[0].value == Decimal("1.24")
    assert tomorrow[0].value == Decimal("0.85")


@pytest.mark.inv("INV-51")
async def test_a_negative_typed_price_is_not_clamped() -> None:
    """A district-heat credit is a negative price like any other (INV-51)."""
    slots = await source(daily={DAY: Decimal("-0.10")}).fetch(DAY)

    assert slots[0].value == Decimal("-0.10")


async def test_a_price_typed_in_minor_units_is_converted() -> None:
    """85 øre and 0.85 kroner are the same price (D1 §5.2)."""
    slots = await ManualSource(
        price=Decimal(85),
        currency="NOK",
        site_currency="NOK",
        tz=OSLO,
        magnitude=Magnitude.MINOR,
    ).fetch(DAY)

    assert slots[0].value == Decimal("0.85")


async def test_a_currency_that_is_not_the_site_s_is_refused() -> None:
    """Even a typed price is not silently converted (D1 §5.2)."""
    with pytest.raises(SourceDataError):
        await ManualSource(
            price=Decimal("0.85"), currency="EUR", site_currency="NOK", tz=OSLO
        ).fetch(DAY)


def test_a_manual_source_has_nothing_to_subscribe_to_and_no_publication() -> None:
    """Nothing fetches and nothing changes, so there is no schedule (D1 §5.1)."""
    manual = source()

    assert manual.publication() is None
    assert manual.entity_ids() == frozenset()
    assert manual.carrier is Carrier.GAS
    assert manual.direction is Direction.IMPORT
