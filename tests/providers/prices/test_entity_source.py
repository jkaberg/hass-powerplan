"""`EntitySource` - one price entity, one format adapter (D1 §3).

What is under test here is the half that is the same for all thirteen rows of
D1 §2's table: reading the state, normalising once, and answering for one *local*
day. The rows themselves are `formats/test_formats.py`.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices import (
    EntitySource,
    SourceEmptyError,
    SourceUnavailableError,
    formats,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from homeassistant.core import HomeAssistant

OSLO = ZoneInfo("Europe/Oslo")
ENTITY = "sensor.nordpool_kwh_no3_nok_3_10_025"

TODAY = date(2026, 9, 19)
TOMORROW = date(2026, 9, 20)
QUARTERS_PER_DAY = 96
FALL_BACK_QUARTERS = 100


def hacs_source(hass: HomeAssistant, *, entity_id: str = ENTITY) -> EntitySource:
    """Build an `EntitySource` over the HACS Nord Pool adapter."""
    return EntitySource(
        hass,
        entity_id=entity_id,
        adapter=formats.build("nordpool_hacs"),
        site_currency="NOK",
        tz=OSLO,
    )


def put(hass: HomeAssistant, fixture: dict[str, Any]) -> None:
    """Put a format fixture into the state machine."""
    hass.states.async_set(fixture["entity_id"], fixture["state"], fixture["attributes"])


async def test_fetch_answers_for_one_local_day(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The entity holds two days; a fetch returns the one it was asked for."""
    put(hass, format_fixture("nordpool_hacs_no3_quarter"))
    source = hacs_source(hass)

    today = await source.fetch(TODAY)
    tomorrow = await source.fetch(TOMORROW)

    assert len(today) == len(tomorrow) == QUARTERS_PER_DAY
    assert today[0].start.astimezone(OSLO).date() == TODAY
    assert tomorrow[0].start.astimezone(OSLO).date() == TOMORROW
    assert today[-1].end == tomorrow[0].start


@pytest.mark.inv("INV-7")
async def test_a_local_day_can_be_twenty_five_hours_long(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The day filter is a local day, not 24 hours of UTC (INV-7, D1 §5.8)."""
    put(hass, format_fixture("nordpool_hacs_dst_autumn"))

    slots = await hacs_source(hass).fetch(date(2026, 10, 25))

    assert sum((slot.end - slot.start).total_seconds() for slot in slots) / 3600 == 25.0


async def test_an_entity_with_no_prices_for_that_day_is_empty_not_wrong(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A day the entity knows nothing about is `SourceEmptyError` (D1 §8)."""
    put(hass, format_fixture("nordpool_hacs_no3_quarter"))

    with pytest.raises(SourceEmptyError):
        await hacs_source(hass).fetch(date(2026, 9, 25))


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_an_unavailable_price_entity_degrades(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]], state: str
) -> None:
    """An entity that cannot answer fails its source, retryably (D1 §8)."""
    fixture = format_fixture("nordpool_hacs_no3_quarter")
    fixture["state"] = state
    put(hass, fixture)

    with pytest.raises(SourceUnavailableError) as raised:
        await hacs_source(hass).fetch(TODAY)
    assert raised.value.retryable is True


async def test_a_price_entity_that_does_not_exist_degrades(hass: HomeAssistant) -> None:
    """A deleted entity is unavailable, not a crash (INV-53's price half)."""
    with pytest.raises(SourceUnavailableError, match="does not exist"):
        await hacs_source(hass, entity_id="sensor.gone").fetch(TODAY)


async def test_an_entity_source_has_no_publication_time(hass: HomeAssistant) -> None:
    """A continuous source is re-read on its state change, not on a clock (D1 §5.1)."""
    assert hacs_source(hass).publication() is None


async def test_the_native_unit_is_read_from_the_live_state(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """Several integrations let the user change the unit, so it is read (D1 §4)."""
    fixture = format_fixture("nordpool_hacs_dst_spring")
    fixture["attributes"]["unit"] = "MWh"
    put(hass, fixture)

    assert hacs_source(hass).native_unit() == ("NOK", EnergyUnit.MWH, Magnitude.MAJOR)


async def test_the_native_unit_of_an_unavailable_entity_is_the_site_s(hass: HomeAssistant) -> None:
    """With nothing to read, the site's own currency per kWh is the honest answer."""
    assert hacs_source(hass, entity_id="sensor.gone").native_unit() == (
        "NOK",
        EnergyUnit.KWH,
        Magnitude.MAJOR,
    )


async def test_entity_ids_is_the_one_entity_the_runtime_subscribes_to(hass: HomeAssistant) -> None:
    """The source registers nothing; the runtime does (INV-3, D7 §5.3)."""
    assert hacs_source(hass).entity_ids() == frozenset({ENTITY})
