"""Every registered price format, through the site flow to a parsed curve (D1 §9 18).

PLAN §7 dec. 22: a row ships only when the site flow can configure it end to end.
An audit found five of fourteen keys that raised at setup, because
`_price_source` built every format with no options and wrapped the action rows in
an `EntitySource`. So this test is parametrised over `formats.keys()` itself, not
over a list: a row registered tomorrow is in it without anyone adding it.

Each case registers the row's entity the way its integration does - a config
entry, a device, a registry entry on the row's platform, the fixture's state -
and, for an action row, the row's response action. It then walks the site flow:
the agreement question, the sensor, the format-options step where the row needs
one, and the rest to the entry. The stored source is built by `_price_source`,
and the first fetch must give slots for the fixture's local day.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import CONF_PRICES
from custom_components.powerplan.providers.prices import formats
from custom_components.powerplan.runtime import _price_source
from tests.flows.test_site_flow import ELECTRICAL_NO, _answer, _configure, _followups, _start, _tail

if TYPE_CHECKING:
    from collections.abc import Callable

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

#: Every fixture holds the local day 2026-09-19; the two state-priced rows price
#: the slot the entity was written in, so the clock is inside that day.
FROZEN = "2026-09-19T11:20:30+00:00"
DAY = date(2026, 9, 19)


@dataclass(frozen=True)
class Case:
    """How one row's entity looks in a house, and what its flow asks."""

    fixture: str
    tz: str
    currency: str
    #: The entity an action row's sensor is: `(entity_id, unit)`.
    sensor: tuple[str, str] | None = None
    #: The format picked by hand, where the platform does not decide it.
    pick: str | None = None
    #: The answers on `prices_format`, or `None` when the step must not appear.
    options: Mapping[str, Any] | None = None
    #: Rows whose platform is `None` are registered under this one.
    platform: str | None = None
    #: The local day the fixture prices.
    day: date = DAY


CASES: dict[str, Case] = {
    "amber": Case("amber", "Australia/Sydney", "AUD"),
    "comed": Case("comed", "America/Chicago", "USD"),
    "cz_energy_spot_prices": Case("cz_energy_spot_prices", "Europe/Prague", "CZK"),
    "easyenergy_action": Case(
        "easyenergy_action",
        "Europe/Amsterdam",
        "EUR",
        sensor=("sensor.easyenergy_price", "EUR/kWh"),
    ),
    "energidataservice": Case("energidataservice", "Europe/Copenhagen", "DKK"),
    "energyzero_action": Case(
        "energyzero_action",
        "Europe/Amsterdam",
        "EUR",
        sensor=("sensor.energyzero_price", "EUR/kWh"),
    ),
    "entsoe": Case("entsoe", "Europe/Amsterdam", "EUR"),
    "epex_spot": Case("epex_spot", "Europe/Berlin", "EUR"),
    "frank_energie": Case("frank_energie", "Europe/Amsterdam", "EUR"),
    "generic_list": Case(
        "generic_list",
        "Europe/Oslo",
        "NOK",
        platform="template",
        pick="generic_list",
        day=date(2026, 9, 20),
        options={
            "attribute": "forecast",
            "start_key": "from",
            "end_key": "to",
            "value_key": "price",
            "currency": "NOK",
            "magnitude": "minor",
        },
    ),
    "hourly_attributes": Case(
        "hourly_attributes",
        "Europe/Oslo",
        "NOK",
        platform="template",
        pick="hourly_attributes",
        options={"currency": "NOK", "today_prefix": "rate_", "tomorrow_prefix": "rate_next_"},
    ),
    "nordpool_core": Case("nordpool_core", "Europe/Oslo", "NOK"),
    "nordpool_hacs": Case("nordpool_hacs_no3_quarter", "Europe/Oslo", "NOK", pick="nordpool_hacs"),
    "octopus_energy": Case("octopus_energy", "Europe/London", "GBP", options={}),
    "pvpc": Case("pvpc", "Europe/Madrid", "EUR"),
    "stromligning": Case("stromligning", "Europe/Copenhagen", "DKK"),
    "tge": Case("tge", "Europe/Warsaw", "PLN"),
    "tibber_action": Case(
        "tibber_action", "Europe/Oslo", "NOK", sensor=("sensor.villa_electricity_price", "NOK/kWh")
    ),
    "tibber_prices": Case(
        "tibber_prices", "Europe/Oslo", "NOK", sensor=("sensor.tibber_current_price", "NOK/kWh")
    ),
    "zonneplan_one": Case("zonneplan_one", "Europe/Amsterdam", "EUR"),
}


def test_every_registered_row_has_a_case() -> None:
    """The parametrisation is the registry: a new row without a case fails here."""
    assert set(CASES) == set(formats.keys())


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "formats"


@pytest.fixture
def format_fixture() -> Callable[[str], dict[str, Any]]:
    """Return a loader for one hand-written payload under `tests/fixtures/formats/`."""

    def load(name: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        return loaded

    return load


@pytest.fixture(autouse=True)
def _at_a_known_instant(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock inside every fixture's local day."""
    freezer.move_to(FROZEN)


def _register_entity(
    hass: HomeAssistant, *, platform: str, entity_id: str, state: str, attributes: Mapping[str, Any]
) -> str:
    """Register one entity as its integration does and return its config entry id."""
    entry = MockConfigEntry(domain=platform, title=platform, entry_id=f"{platform}-entry-id")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(platform, "home")}, name="Home"
    )
    domain, object_id = entity_id.split(".", 1)
    er.async_get(hass).async_get_or_create(
        domain,
        platform,
        object_id,
        suggested_object_id=object_id,
        config_entry=entry,
        device_id=device.id,
        unit_of_measurement=attributes.get("unit_of_measurement"),
    )
    hass.states.async_set(entity_id, state, dict(attributes))
    return entry.entry_id


def _register_action(hass: HomeAssistant, fixture: Mapping[str, Any]) -> None:
    """Register the row's response action, `SupportsResponse.ONLY` as upstream does."""
    domain, service = str(fixture["service"]).split(".")
    response = fixture["response"]

    async def handler(call: ServiceCall) -> ServiceResponse:
        del call
        return copy.deepcopy(response)

    hass.services.async_register(domain, service, handler, supports_response=SupportsResponse.ONLY)


def _next_day(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Return Octopus's `next_day_rates` sibling: the same rates a day later."""
    shifted = copy.deepcopy(dict(fixture))
    shifted["entity_id"] = str(fixture["entity_id"]).replace(
        "_current_day_rates", "_next_day_rates"
    )
    for rate in shifted["attributes"]["rates"]:
        for key in ("start", "end"):
            rate[key] = (datetime.fromisoformat(rate[key]) + timedelta(days=1)).isoformat()
    return shifted


async def _through_the_sensor(
    hass: HomeAssistant, key: str, case: Case, entity_id: str
) -> dict[str, Any]:
    """Walk the flow from the agreement question to the stored entry."""
    result = await _start(hass, "price_only")
    result = await _answer(hass, result, **ELECTRICAL_NO)
    assert result["step_id"] == "prices"
    result = await _answer(hass, result, source="entity")
    assert result["step_id"] == "prices_entity"
    picked: dict[str, Any] = {"entity_id": entity_id}
    if case.pick is not None:
        picked["format"] = case.pick
    result = await _answer(hass, result, **picked)

    if case.options is None:
        assert result["step_id"] == "modifiers", f"{key} asked what its entity answers"
    else:
        assert result["step_id"] == "prices_format", result
        # The row's name in the household's words, never its key (review §0).
        assert result["description_placeholders"]["format"] != key
        if formats.tomorrow_entity(key) is not None:
            shown = result["data_schema"]({})
            assert str(shown["second_entity_id"]).endswith("_next_day_rates")
        result = await _answer(hass, result, **case.options)
    result = await _followups(hass, result)
    result = await _tail(hass, result)
    return await _answer(hass, result, start_in_observe=True)


@pytest.mark.parametrize("key", sorted(CASES))
async def test_every_row_goes_through_the_flow_to_a_parsed_curve(
    hass: HomeAssistant,
    key: str,
    persons: list[str],
    format_fixture: Callable[[str], dict[str, Any]],
) -> None:
    """D1 §9 18: the flow stores the row, `_price_source` builds it, the fetch parses."""
    del persons
    _configure(hass)
    case = CASES[key]
    platform = formats.entry(key).platform or case.platform
    assert platform is not None
    fixture = format_fixture(case.fixture)

    if formats.entry(key).kind is formats.FormatKind.ACTION:
        assert case.sensor is not None
        entity_id, unit = case.sensor
        entry_id = _register_entity(
            hass,
            platform=platform,
            entity_id=entity_id,
            state="1.0",
            attributes={"unit_of_measurement": unit},
        )
        _register_action(hass, fixture)
    else:
        entity_id = str(fixture["entity_id"])
        entry_id = _register_entity(
            hass,
            platform=platform,
            entity_id=entity_id,
            state=str(fixture["state"]),
            attributes=fixture["attributes"],
        )
    if formats.tomorrow_entity(key) is not None:
        tomorrow = _next_day(fixture)
        device_id = er.async_get(hass).async_get(entity_id).device_id  # type: ignore[union-attr]
        domain, object_id = str(tomorrow["entity_id"]).split(".", 1)
        er.async_get(hass).async_get_or_create(
            domain,
            platform,
            object_id,
            suggested_object_id=object_id,
            config_entry=hass.config_entries.async_get_entry(entry_id),
            device_id=device_id,
        )
        hass.states.async_set(tomorrow["entity_id"], tomorrow["state"], tomorrow["attributes"])

    result = await _through_the_sensor(hass, key, case, entity_id)
    await hass.async_block_till_done()

    (row,) = result["data"][CONF_PRICES]["sources"]
    assert row["options"]["format"] == key
    stored = row["options"].get("format_options") or {}
    if "config_entry" in {field.key for field in formats.entry(key).schema}:
        # D1 §6: the picked entity's own config entry, never asked.
        assert stored["config_entry"] == entry_id
    source = _price_source(hass, row, currency=case.currency, tz=ZoneInfo(case.tz))

    slots = await source.fetch(case.day)

    assert slots, f"{key} stored a source that parses nothing"
    assert {slot.currency for slot in slots} == {case.currency}
    if formats.tomorrow_entity(key) is not None:
        assert await source.fetch(case.day + timedelta(days=1)), "tomorrow's entity is not read"


async def test_the_detected_format_is_the_one_stored(
    hass: HomeAssistant, persons: list[str], format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """Left empty, the format is the detected one - never the select's first option."""
    del persons
    _configure(hass)
    fixture = format_fixture("entsoe")
    _register_entity(
        hass,
        platform="entsoe",
        entity_id=fixture["entity_id"],
        state=fixture["state"],
        attributes=fixture["attributes"],
    )

    result = await _through_the_sensor(hass, "entsoe", CASES["entsoe"], fixture["entity_id"])

    options = result["data"][CONF_PRICES]["sources"][0]["options"]
    assert options["format"] == options["detected_format"] == "entsoe"


async def test_an_entity_no_row_claims_is_refused_inline(hass: HomeAssistant) -> None:
    """Nothing says how to read it, so nothing is guessed (D1 §6)."""
    _configure(hass)
    hass.states.async_set("sensor.somewhere", "1", {"unit_of_measurement": "NOK/kWh"})
    result = await _start(hass, "price_only")
    result = await _answer(hass, result, **ELECTRICAL_NO)
    result = await _answer(hass, result, source="entity")

    result = await _answer(hass, result, entity_id="sensor.somewhere")

    assert result["step_id"] == "prices_entity"
    assert result["errors"] == {"format": "price_format_unknown"}


async def test_tibber_names_the_home_only_when_the_account_has_two(
    hass: HomeAssistant, persons: list[str], format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """`get_prices` keys its answer by home; with two, the picked sensor's is named."""
    del persons
    _configure(hass)
    entry = MockConfigEntry(domain="tibber", title="Tibber", entry_id="tibber-entry-id")
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    for name in ("Villa", "Cabin"):
        device = devices.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("tibber", name)},
            name=name,
            model="Price Sensor",
        )
        object_id = f"electricity_price_{name.lower()}"
        er.async_get(hass).async_get_or_create(
            "sensor",
            "tibber",
            object_id,
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
            unit_of_measurement="NOK/kWh",
        )
        hass.states.async_set(f"sensor.{object_id}", "1.0", {"unit_of_measurement": "NOK/kWh"})
    fixture = format_fixture("tibber_action")
    (only,) = fixture["response"]["prices"]
    fixture["response"]["prices"] = {"Villa": fixture["response"]["prices"][only], "Cabin": []}
    _register_action(hass, fixture)

    result = await _through_the_sensor(
        hass, "tibber_action", CASES["tibber_action"], "sensor.electricity_price_villa"
    )

    row = result["data"][CONF_PRICES]["sources"][0]
    assert row["options"]["format_options"] == {"currency": "NOK", "home": "Villa"}
    source = _price_source(hass, row, currency="NOK", tz=ZoneInfo("Europe/Oslo"))
    assert await source.fetch(DAY)
