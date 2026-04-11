"""Every v1 format adapter parses its fixture (D1 §9 item 1, two rows).

WP1.2 implements two rows of D1 §2's table - `nordpool_hacs` and `generic_list`.
The other ten (`energidataservice`, `entsoe`, `tibber_action`,
`energyzero_action`, `octopus_energy`, `amber`, `pvpc`, `comed`, `tge`,
`hourly_attributes`) are WP4.4 and bring their own fixtures.

The reference house runs the **core** Nord Pool integration, whose sensors carry
no forecast attributes at all (`tests/fixtures/captured/nordpool_core_no3.json`
proves it), so there is no captured HACS dump to parse. The HACS payloads are
hand-written from the upstream source, under `tests/fixtures/formats/` and never
under `captured/`, each naming its documentation in a `source` key
(`design/DECISIONS.md` D-0081).
"""

from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.pricing.normalise import (
    EnergyUnit,
    Magnitude,
    check_day_length,
    day_hours,
    gaps,
)
from custom_components.powerplan.providers.prices import (
    SourceDataError,
    SourceParseError,
    formats,
    normalise,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from homeassistant.core import HomeAssistant, State

    from custom_components.powerplan.core.pricing import RawSlot
    from custom_components.powerplan.providers.prices.formats import EntityFormat

OSLO = ZoneInfo("Europe/Oslo")

HOURS_PER_DAY = 24
QUARTERS_PER_DAY = 96
SPRING_FORWARD_HOURS = 23
FALL_BACK_HOURS = 25


def put(hass: HomeAssistant, fixture: dict[str, Any]) -> State:
    """Put a format fixture into the state machine and return its state."""
    hass.states.async_set(fixture["entity_id"], fixture["state"], fixture["attributes"])
    state = hass.states.get(fixture["entity_id"])
    assert state is not None
    return state


def slots(
    adapter: EntityFormat, state: State, *, site_currency: str = "NOK"
) -> tuple[RawSlot, ...]:
    """Parse and normalise one state, the way `EntitySource.fetch` does."""
    parsed = adapter.parse(state)
    return normalise(
        parsed.intervals,
        source=adapter.key,
        currency=parsed.currency,
        site_currency=site_currency,
        energy=parsed.energy,
        magnitude=parsed.magnitude,
        source_tz=OSLO,
        fetched_at=state.last_updated,
    )


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #


def test_the_registry_holds_the_two_rows_wp1_2_implements() -> None:
    """Importing `formats` registers every adapter it ships (D1 §6).

    The whole roster is asserted against D1 §2's own table in
    `test_registry_table.py`; these two are the rows this module covers.
    """
    assert {"generic_list", "nordpool_hacs"} <= set(formats.keys())


def test_a_format_is_built_from_the_options_the_flow_saved() -> None:
    """`build(key, options)` is how the flow constructs an adapter (D1 §6)."""
    adapter = formats.build("generic_list", {"currency": "NOK", "attribute": "forecast"})

    assert adapter.key == "generic_list"
    assert formats.entry("generic_list").schema == adapter.schema


def test_the_flow_detects_a_format_from_the_entity_platform() -> None:
    """`for_platform` is what pre-selects the format in the prices step (D1 §6).

    Two rows claim `nordpool` - the HACS sensor and the core integration - which
    is the case D1 §2 wrote `for_platform` for: the flow gets both candidates and
    asks only when it cannot tell them apart.
    """
    assert formats.for_platform("nordpool") == ("nordpool_core", "nordpool_hacs")
    assert formats.for_platform("nothing_we_know") == ()


# --------------------------------------------------------------------------- #
# nordpool_hacs
# --------------------------------------------------------------------------- #


def test_nordpool_hacs_parses_raw_today_and_raw_tomorrow(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """Both attribute lists are read, in order, as one series (D1 §2 row 1)."""
    fixture = format_fixture("nordpool_hacs_no3_quarter")
    state = put(hass, fixture)

    rows = slots(formats.build("nordpool_hacs"), state)

    assert len(rows) == 2 * QUARTERS_PER_DAY
    assert rows == tuple(sorted(rows, key=lambda row: row.start))
    assert gaps(rows) == ()
    assert {row.currency for row in rows} == {"NOK"}
    assert {row.source for row in rows} == {"nordpool_hacs"}


def test_nordpool_hacs_keeps_the_published_value_exactly(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A price per kWh in the site's own currency passes through undivided."""
    fixture = format_fixture("nordpool_hacs_no3_quarter")
    state = put(hass, fixture)

    rows = slots(formats.build("nordpool_hacs"), state)

    first = fixture["attributes"]["raw_today"][0]
    assert rows[0].value == Decimal(str(first["value"]))
    assert rows[0].start.isoformat() == "2026-09-18T22:00:00+00:00"


@pytest.mark.inv("INV-7")
def test_nordpool_hacs_slot_length_comes_from_the_payload(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A 15-minute day yields 15-minute slots, never an assumed hour (INV-7)."""
    state = put(hass, format_fixture("nordpool_hacs_no3_quarter"))

    rows = slots(formats.build("nordpool_hacs"), state)

    assert {int((row.end - row.start).total_seconds() // 60) for row in rows} == {15}


@pytest.mark.inv("INV-7")
@pytest.mark.parametrize(
    ("fixture_name", "day", "expected_hours"),
    [
        ("nordpool_hacs_dst_spring", (2026, 3, 29), SPRING_FORWARD_HOURS),
        ("nordpool_hacs_dst_autumn", (2026, 10, 25), FALL_BACK_HOURS),
    ],
    ids=["spring_forward", "fall_back"],
)
def test_nordpool_hacs_handles_a_dst_day(
    hass: HomeAssistant,
    format_fixture: Callable[[str], dict[str, Any]],
    fixture_name: str,
    day: tuple[int, int, int],
    expected_hours: int,
) -> None:
    """A DST day is 23 or 25 hours long and MUST be handled (INV-7, D1 §5.8)."""
    state = put(hass, format_fixture(fixture_name))
    local = date_type(*day)

    rows = slots(formats.build("nordpool_hacs"), state)

    assert len(rows) == expected_hours
    assert day_hours(rows, local, OSLO) == pytest.approx(float(expected_hours))
    check_day_length(day_hours(rows, local, OSLO), source="nordpool_hacs")
    assert gaps(rows) == ()


def test_nordpool_hacs_skips_an_hour_it_could_not_price(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A `value: None` entry is a hole for the forecaster, not a zero price."""
    fixture = format_fixture("nordpool_hacs_dst_spring")
    fixture["attributes"]["raw_today"][5]["value"] = None
    state = put(hass, fixture)

    rows = slots(formats.build("nordpool_hacs"), state)

    assert len(rows) == SPRING_FORWARD_HOURS - 1
    assert len(gaps(rows)) == 1


def test_nordpool_hacs_in_mwh_normalises_to_the_same_prices(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The sensor's `price_type` is read, not assumed (D1 §5.2)."""
    per_kwh = format_fixture("nordpool_hacs_dst_spring")
    per_mwh = format_fixture("nordpool_hacs_dst_spring")
    per_mwh["attributes"]["unit"] = "MWh"
    for row in per_mwh["attributes"]["raw_today"]:
        # Scaled in Decimal, so the fixture's own arithmetic invents no digits.
        row["value"] = str(Decimal(str(row["value"])) * 1000)

    kwh_rows = slots(formats.build("nordpool_hacs"), put(hass, per_kwh))
    per_mwh["entity_id"] = "sensor.nordpool_mwh_no3_nok"
    mwh_rows = slots(formats.build("nordpool_hacs"), put(hass, per_mwh))

    assert [row.value for row in mwh_rows] == [row.value for row in kwh_rows]


def test_nordpool_hacs_refuses_a_currency_that_is_not_the_site_s(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """Silently converting prices is worse than refusing them (D1 §5.2)."""
    fixture = format_fixture("nordpool_hacs_dst_spring")
    fixture["attributes"]["currency"] = "EUR"
    state = put(hass, fixture)

    with pytest.raises(SourceDataError, match="EUR"):
        slots(formats.build("nordpool_hacs"), state, site_currency="NOK")


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("currency", None),
        ("unit", "Wh"),
        ("raw_today", "not a list"),
        ("raw_today", [{"start": "yesterday", "end": "today", "value": 1}]),
    ],
    ids=["no_currency", "unknown_price_type", "not_a_list", "unparseable_boundary"],
)
def test_nordpool_hacs_fails_loudly_when_the_shape_changes(
    hass: HomeAssistant,
    format_fixture: Callable[[str], dict[str, Any]],
    attribute: str,
    value: Any,
) -> None:
    """An integration update that moves the prices fails the source (D1 §8)."""
    fixture = format_fixture("nordpool_hacs_dst_spring")
    fixture["attributes"][attribute] = value
    state = put(hass, fixture)

    with pytest.raises(SourceParseError):
        slots(formats.build("nordpool_hacs"), state)


# --------------------------------------------------------------------------- #
# generic_list
# --------------------------------------------------------------------------- #


def generic(**options: Any) -> EntityFormat:
    """Build `generic_list` over the fixture's own attribute and keys."""
    return formats.build(
        "generic_list",
        {
            "currency": "NOK",
            "attribute": "forecast",
            "start_key": "from",
            "end_key": "to",
            "value_key": "price",
            "magnitude": Magnitude.MINOR,
            **options,
        },
    )


def test_generic_list_reads_the_configured_attribute_and_keys(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The row that makes an unknown integration usable (D1 §2, last row)."""
    fixture = format_fixture("generic_list")
    state = put(hass, fixture)

    rows = slots(generic(), state)

    published = fixture["attributes"]["forecast"]
    assert len(rows) == len(published)
    assert rows[0].start.isoformat() == "2026-09-20T04:00:00+00:00"
    assert {int((row.end - row.start).total_seconds() // 60) for row in rows} == {60}


def test_generic_list_converts_minor_units_to_major(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """øre per kWh becomes kroner per kWh, exactly (D1 §5.2)."""
    fixture = format_fixture("generic_list")
    state = put(hass, fixture)

    rows = slots(generic(), state)

    published = fixture["attributes"]["forecast"]
    assert rows[0].value == Decimal(str(published[0]["price"])) / 100


@pytest.mark.inv("INV-51")
def test_generic_list_does_not_clamp_a_negative_price(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A negative slot is one in which consuming is paid for (INV-51)."""
    fixture = format_fixture("generic_list")
    fixture["attributes"]["forecast"][0]["price"] = -12.5
    state = put(hass, fixture)

    rows = slots(generic(), state)

    assert rows[0].value == Decimal("-0.125")


@pytest.mark.inv("INV-7")
def test_generic_list_without_an_end_key_takes_the_next_start(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """A source that publishes only starts still gets real slot lengths (INV-7)."""
    fixture = format_fixture("generic_list")
    for row in fixture["attributes"]["forecast"]:
        del row["to"]
    state = put(hass, fixture)

    rows = slots(generic(end_key=""), state)

    assert {int((row.end - row.start).total_seconds() // 60) for row in rows} == {60}
    assert gaps(rows) == ()


def test_generic_list_in_kwh_major_is_the_default(
    hass: HomeAssistant, format_fixture: Callable[[str], dict[str, Any]]
) -> None:
    """The unit and magnitude are configuration, and default to kWh major."""
    adapter = formats.build(
        "generic_list",
        {
            "currency": "NOK",
            "attribute": "forecast",
            "start_key": "from",
            "end_key": "to",
            "value_key": "price",
        },
    )
    state = put(hass, format_fixture("generic_list"))

    parsed = adapter.parse(state)

    assert parsed.energy is EnergyUnit.KWH
    assert parsed.magnitude is Magnitude.MAJOR


@pytest.mark.parametrize(
    ("attribute", "rows"),
    [
        ("forecast", None),
        ("forecast", {"not": "a list"}),
        ("forecast", ["not a slot"]),
        ("forecast", [{"from": "2026-09-20T06:00:00+02:00", "to": "x", "price": 1}]),
        ("forecast", [{"from": "2026-09-20T06:00:00+02:00", "to": None}]),
    ],
    ids=["missing", "not_a_list", "not_a_slot", "bad_boundary", "no_price"],
)
def test_generic_list_fails_loudly_on_a_shape_it_cannot_read(
    hass: HomeAssistant,
    format_fixture: Callable[[str], dict[str, Any]],
    attribute: str,
    rows: Any,
) -> None:
    """A misconfigured or changed attribute fails the source (D1 §8)."""
    fixture = format_fixture("generic_list")
    if rows is None:
        del fixture["attributes"][attribute]
    else:
        fixture["attributes"][attribute] = rows
    state = put(hass, fixture)

    with pytest.raises(SourceParseError):
        slots(generic(), state)


# --------------------------------------------------------------------------- #
# every fixture names where its shape came from (D-0081)
# --------------------------------------------------------------------------- #


FORMAT_FIXTURES = sorted(
    path.stem
    for path in (Path(__file__).resolve().parents[3] / "fixtures" / "formats").glob("*.json")
)


def test_there_are_fixtures_to_check() -> None:
    """Guard the guard: a glob over nothing proves nothing."""
    assert len(FORMAT_FIXTURES) >= 6, FORMAT_FIXTURES


@pytest.mark.parametrize("name", FORMAT_FIXTURES)
def test_a_hand_written_fixture_names_its_documentation(
    format_fixture: Callable[[str], dict[str, Any]], name: str
) -> None:
    """Nothing under `formats/` is captured, so each one says where it came from."""
    assert format_fixture(name)["source"].strip()
