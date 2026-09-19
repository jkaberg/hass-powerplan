"""The attribute-backed rows of D1 §2's table (D1 §9 item 1).

`nordpool_hacs` and `generic_list` are in `test_formats.py`. Here are the
rows WP4.4 adds that read an entity's attributes or its state:
`nordpool_core`, `energidataservice`, `entsoe`, `tge`, `octopus_energy`, `amber`,
`pvpc`, `comed` and `hourly_attributes`, and the ones WP4.7 adds: `epex_spot`,
`zonneplan_one`, `frank_energie`, `stromligning` and `cz_energy_spot_prices`. The
action-backed rows are in `test_action_formats.py`.

Every row answers the same five questions, so they are asked once, from a table:
does the fixture parse into normalised `RawSlot`s; is each slot as long as the
payload says (INV-7); is the row's unit - MWh, cents, pence, dollars - converted
to major units per kWh; does a negative price stay negative (INV-51); and is a
currency that is not the site's refused rather than silently converted (D1 §5.2).
The row-specific tests below that are the quirks each integration actually has: a
25-hour Octopus day, PVPC's `price_02h_d`, the ComEd sensor that publishes one
hour and no forecast at all.

`WRITE` is how a row's first, fifth or fifteenth published price is overwritten
without the test knowing where that price lives - the only per-row knowledge the
table carries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date as date_type
from datetime import tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.pricing.normalise import (
    check_day_length,
    day_hours,
    gaps,
)
from custom_components.powerplan.providers.prices import (
    EntitySource,
    SourceDataError,
    SourceParseError,
    formats,
)

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant, State

    from custom_components.powerplan.core.pricing import RawSlot

OSLO = ZoneInfo("Europe/Oslo")
COPENHAGEN = ZoneInfo("Europe/Copenhagen")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
WARSAW = ZoneInfo("Europe/Warsaw")
LONDON = ZoneInfo("Europe/London")
SYDNEY = ZoneInfo("Australia/Sydney")
MADRID = ZoneInfo("Europe/Madrid")
CHICAGO = ZoneInfo("America/Chicago")
PRAGUE = ZoneInfo("Europe/Prague")

#: Every fixture is the local day 2026-09-19 (plus its tomorrow); the clock is
#: frozen inside it so the two rows whose slot boundary is the entity's own
#: timestamp - `nordpool_core`, `comed` - have a boundary to be tested against.
FROZEN = "2026-09-19T11:20:30+00:00"
DAY = date_type(2026, 9, 19)

FALL_BACK_HOURS = 25
HALF_HOURS_IN_A_FALL_BACK_DAY = 50

# --------------------------------------------------------------------------- #
# the table: one row of D1 §2 per `Row`
# --------------------------------------------------------------------------- #


def write_list(attribute: str, key: str) -> Callable[[dict[str, Any], int, Any], None]:
    """Return a writer for the `index`-th entry of an attribute's list."""

    def write(fixture: dict[str, Any], index: int, value: Any) -> None:
        fixture["attributes"][attribute][index][key] = value

    return write


def write_hour_key(prefix: str) -> Callable[[dict[str, Any], int, Any], None]:
    """Return a writer for the `<prefix>{HH}h` attribute of local hour `index`."""

    def write(fixture: dict[str, Any], index: int, value: Any) -> None:
        fixture["attributes"][f"{prefix}{index:02d}h"] = value

    return write


def write_nested(attribute: str, *path: str) -> Callable[[dict[str, Any], int, Any], None]:
    """Return a writer for a nested key of the `index`-th entry (`zonneplan_one`)."""

    def write(fixture: dict[str, Any], index: int, value: Any) -> None:
        found = fixture["attributes"][attribute][index]
        for step in path[:-1]:
            found = found[step]
        found[path[-1]] = value

    return write


def write_start_key(fixture: dict[str, Any], index: int, value: Any) -> None:
    """Overwrite the attribute keyed by local hour `index`'s start (`cz_energy_spot_prices`)."""
    fixture["attributes"][f"2026-09-19T{index:02d}:00:00+02:00"] = value


def write_state(fixture: dict[str, Any], index: int, value: Any) -> None:
    """Overwrite the state itself - the price of the rows that publish no list."""
    assert index == 0, "a state-priced row publishes exactly one price"
    fixture["state"] = value


@dataclass(frozen=True)
class Row:
    """One attribute-backed row of D1 §2's table, and what it must produce."""

    key: str
    fixture: str
    options: Mapping[str, Any]
    site_currency: str
    tz: tzinfo
    slots: int
    minutes: frozenset[int]
    first_start: str
    factor: Decimal
    write: Callable[[dict[str, Any], int, Any], None]
    attribute: str | None = None
    bounded: bool = False

    @property
    def state_priced(self) -> bool:
        """Whether the price is the state, so there is no list and no hole."""
        return self.attribute is None


ROWS: tuple[Row, ...] = (
    Row(
        key="nordpool_core",
        fixture="nordpool_core",
        options={},
        site_currency="NOK",
        tz=OSLO,
        slots=1,
        minutes=frozenset({15}),
        first_start="2026-09-19T11:15:00+00:00",
        factor=Decimal(1),
        write=write_state,
    ),
    Row(
        key="energidataservice",
        fixture="energidataservice",
        options={},
        site_currency="DKK",
        tz=COPENHAGEN,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_list("raw_today", "price"),
        attribute="raw_today",
    ),
    Row(
        key="entsoe",
        fixture="entsoe",
        options={},
        site_currency="EUR",
        tz=AMSTERDAM,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_list("prices_today", "price"),
        attribute="prices_today",
    ),
    Row(
        key="tge",
        fixture="tge",
        options={},
        site_currency="PLN",
        tz=WARSAW,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal("0.001"),
        write=write_list("prices_today", "price"),
        attribute="prices_today",
    ),
    Row(
        key="octopus_energy",
        fixture="octopus_energy",
        options={},
        site_currency="GBP",
        tz=LONDON,
        slots=48,
        minutes=frozenset({30}),
        first_start="2026-09-18T23:00:00+00:00",
        factor=Decimal(1),
        write=write_list("rates", "value_inc_vat"),
        attribute="rates",
        bounded=True,
    ),
    Row(
        key="amber",
        fixture="amber",
        options={},
        site_currency="AUD",
        tz=SYDNEY,
        slots=48,
        minutes=frozenset({30}),
        first_start="2026-09-18T14:00:00+00:00",
        factor=Decimal(1),
        write=write_list("forecasts", "per_kwh"),
        attribute="forecasts",
        bounded=True,
    ),
    Row(
        key="pvpc",
        fixture="pvpc",
        options={},
        site_currency="EUR",
        tz=MADRID,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_hour_key("price_"),
        attribute="price_00h",
    ),
    Row(
        key="comed",
        fixture="comed",
        options={},
        site_currency="USD",
        tz=CHICAGO,
        slots=1,
        minutes=frozenset({60}),
        first_start="2026-09-19T11:00:00+00:00",
        factor=Decimal("0.01"),
        write=write_state,
    ),
    Row(
        key="epex_spot",
        fixture="epex_spot",
        options={},
        site_currency="EUR",
        tz=AMSTERDAM,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_list("data", "price_per_kwh"),
        attribute="data",
        bounded=True,
    ),
    Row(
        key="zonneplan_one",
        fixture="zonneplan_one",
        options={},
        site_currency="EUR",
        tz=AMSTERDAM,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal("0.0000001"),
        write=write_nested("forecast", "price_tax_included", "amount"),
        attribute="forecast",
        bounded=True,
    ),
    Row(
        key="frank_energie",
        fixture="frank_energie",
        options={},
        site_currency="EUR",
        tz=AMSTERDAM,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_list("prices", "price"),
        attribute="prices",
        bounded=True,
    ),
    Row(
        key="stromligning",
        fixture="stromligning",
        options={},
        site_currency="DKK",
        tz=COPENHAGEN,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_list("prices", "price"),
        attribute="prices",
        bounded=True,
    ),
    Row(
        key="cz_energy_spot_prices",
        fixture="cz_energy_spot_prices",
        options={},
        site_currency="CZK",
        tz=PRAGUE,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_start_key,
        attribute="2026-09-19T00:00:00+02:00",
    ),
    Row(
        key="hourly_attributes",
        fixture="hourly_attributes",
        options={"currency": "NOK", "today_prefix": "rate_", "tomorrow_prefix": "rate_next_"},
        site_currency="NOK",
        tz=OSLO,
        slots=48,
        minutes=frozenset({60}),
        first_start="2026-09-18T22:00:00+00:00",
        factor=Decimal(1),
        write=write_hour_key("rate_"),
        attribute="rate_00h",
    ),
)

IDS = [row.key for row in ROWS]
LISTED = [row for row in ROWS if not row.state_priced]
LISTED_IDS = [row.key for row in LISTED]
BOUNDED = [row for row in LISTED if row.bounded]
BOUNDED_IDS = [row.key for row in BOUNDED]
UNBOUNDED = [row for row in LISTED if not row.bounded]
UNBOUNDED_IDS = [row.key for row in UNBOUNDED]


@pytest.fixture(autouse=True)
def _at_a_known_instant(freezer: FrozenDateTimeFactory) -> None:
    """Freeze the clock at an instant inside every fixture's local day.

    Two rows publish no timestamp at all - `nordpool_core` and `comed` price the
    slot the entity was last written in - and two build their timestamps from the
    local date, so the clock has to be the test's, not the machine's.
    """
    freezer.move_to(FROZEN)


def parse(
    row: Row,
    fixture: dict[str, Any],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
    *,
    site_currency: str | None = None,
) -> tuple[RawSlot, ...]:
    """Put `fixture` into the state machine and normalise it through `row`."""
    state = put_state(fixture)
    return normalise_row(
        formats.build(row.key, dict(row.options)),
        state,
        site_currency=site_currency or row.site_currency,
        source_tz=row.tz,
    )


# --------------------------------------------------------------------------- #
# what every row must do
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_row_parses_its_fixture_into_normalised_slots(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """D1 §9 item 1: every adapter parses its fixture into `RawSlot`s."""
    rows = parse(row, format_fixture(row.fixture), put_state, normalise_row)

    assert len(rows) == row.slots
    assert rows == tuple(sorted(rows, key=lambda slot: slot.start))
    assert gaps(rows) == ()
    assert {slot.currency for slot in rows} == {row.site_currency}
    assert {slot.source for slot in rows} == {row.key}


@pytest.mark.inv("INV-7")
@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_row_takes_its_slot_length_from_the_payload(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """A slot is as long as the source says, never an assumed hour (INV-7)."""
    rows = parse(row, format_fixture(row.fixture), put_state, normalise_row)

    assert {int((slot.end - slot.start).total_seconds() // 60) for slot in rows} == set(row.minutes)
    assert rows[0].start.isoformat() == row.first_start


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_row_converts_its_unit_to_major_units_per_kwh(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """MWh, cents and pence all become major units per kWh (D1 §5.2)."""
    fixture = format_fixture(row.fixture)
    row.write(fixture, 0, "100")

    rows = parse(row, fixture, put_state, normalise_row)

    assert rows[0].value == Decimal(100) * row.factor


@pytest.mark.inv("INV-51")
@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_row_does_not_clamp_a_negative_price(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """A negative slot is one in which consuming is paid for (INV-51)."""
    fixture = format_fixture(row.fixture)
    row.write(fixture, 0, "-100")

    rows = parse(row, fixture, put_state, normalise_row)

    assert rows[0].value == Decimal(-100) * row.factor


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_row_refuses_a_currency_that_is_not_the_site_s(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """Silently converting prices is worse than refusing them (D1 §5.2)."""
    with pytest.raises(SourceDataError):
        parse(row, format_fixture(row.fixture), put_state, normalise_row, site_currency="XTS")


@pytest.mark.parametrize("row", ROWS, ids=IDS)
def test_a_row_fails_loudly_when_a_price_is_not_a_price(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """An integration that changes its payload fails the source (D1 §8)."""
    fixture = format_fixture(row.fixture)
    row.write(fixture, 0, "wednesday")

    with pytest.raises(SourceParseError):
        parse(row, fixture, put_state, normalise_row)


@pytest.mark.parametrize("row", BOUNDED, ids=BOUNDED_IDS)
def test_a_row_with_interval_bounds_reports_a_hole(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """A price the source could not publish is a hole for the forecaster."""
    fixture = format_fixture(row.fixture)
    row.write(fixture, 5, None)

    rows = parse(row, fixture, put_state, normalise_row)

    assert len(rows) == row.slots - 1
    assert len(gaps(rows)) == 1


@pytest.mark.inv("INV-7")
@pytest.mark.parametrize("row", UNBOUNDED, ids=UNBOUNDED_IDS)
def test_a_row_that_publishes_only_starts_absorbs_a_hole(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """A source that publishes starts cannot express a hole, and none is invented.

    These rows give a start and a price and nothing else, so a slot ends where the
    next one begins (D1 §5.2, INV-7) and a price the source omitted widens the slot
    before it. That is the honest reading: assuming 60 minutes and calling the rest
    a hole would be a guess about a source that said nothing, which is exactly what
    INV-7 forbids. The published prices themselves are never invented - the dropped
    entry does not become a zero.
    """
    fixture = format_fixture(row.fixture)
    row.write(fixture, 5, None)

    rows = parse(row, fixture, put_state, normalise_row)

    assert len(rows) == row.slots - 1
    assert gaps(rows) == ()
    widened = [slot for slot in rows if (slot.end - slot.start).total_seconds() // 60 == 120]
    assert len(widened) == 1


@pytest.mark.parametrize("row", LISTED, ids=LISTED_IDS)
def test_a_row_fails_loudly_when_its_attribute_is_no_longer_a_list(
    row: Row,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """The payload's shape is asserted, never assumed (D1 §8, D-0088)."""
    fixture = format_fixture(row.fixture)
    assert row.attribute is not None
    fixture["attributes"][row.attribute] = "not a list of slots"

    with pytest.raises(SourceParseError):
        parse(row, fixture, put_state, normalise_row)


# --------------------------------------------------------------------------- #
# octopus_energy - the DST day D1 §9 item 1 names
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-7")
def test_octopus_energy_handles_a_twenty_five_hour_day(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """The 25-hour local day is 50 half-hour rates (D1 §9 item 1, §5.8)."""
    state = put_state(format_fixture("octopus_energy_dst_autumn"))

    rows = normalise_row(
        formats.build("octopus_energy"), state, site_currency="GBP", source_tz=LONDON
    )

    local = date_type(2026, 10, 25)
    assert len(rows) == HALF_HOURS_IN_A_FALL_BACK_DAY
    assert day_hours(rows, local, LONDON) == pytest.approx(float(FALL_BACK_HOURS))
    check_day_length(day_hours(rows, local, LONDON), source="octopus_energy")
    assert gaps(rows) == ()
    assert {int((slot.end - slot.start).total_seconds() // 60) for slot in rows} == {30}


@pytest.mark.inv("INV-51")
def test_octopus_energy_keeps_the_agile_afternoon_negative(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """Agile really does go negative, and the fixture really says so (INV-51)."""
    state = put_state(format_fixture("octopus_energy"))

    rows = normalise_row(
        formats.build("octopus_energy"), state, site_currency="GBP", source_tz=LONDON
    )

    negative = [slot for slot in rows if slot.value < 0]
    assert len(negative) == 2
    assert {slot.start.astimezone(LONDON).hour for slot in negative} == {13}


async def test_octopus_energy_reads_a_local_day_through_the_entity_source(
    hass: HomeAssistant,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
) -> None:
    """The row works through `EntitySource`, not only through `parse` (D1 §3)."""
    fixture = format_fixture("octopus_energy")
    put_state(fixture)
    source = EntitySource(
        hass,
        entity_id=fixture["entity_id"],
        adapter=formats.build("octopus_energy"),
        site_currency="GBP",
        tz=LONDON,
    )

    slots = await source.fetch(DAY)

    assert len(slots) == 48
    assert slots[0].start.isoformat() == "2026-09-18T23:00:00+00:00"


# --------------------------------------------------------------------------- #
# pvpc - the hour keys, including the repeated one
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-7")
def test_pvpc_reads_the_repeated_hour_of_a_fall_back_day(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
    freezer: FrozenDateTimeFactory,
) -> None:
    """`price_02h` and `price_02h_d` are two different instants (INV-7, D1 §5.8)."""
    freezer.move_to("2026-10-25T09:20:30+00:00")
    state = put_state(format_fixture("pvpc_dst_autumn"))

    rows = normalise_row(formats.build("pvpc"), state, site_currency="EUR", source_tz=MADRID)

    local = date_type(2026, 10, 25)
    assert len(rows) == FALL_BACK_HOURS
    assert day_hours(rows, local, MADRID) == pytest.approx(float(FALL_BACK_HOURS))
    assert gaps(rows) == ()
    repeated = [slot for slot in rows if slot.start.astimezone(MADRID).hour == 2]
    assert len(repeated) == 2
    assert repeated[1].start - repeated[0].start == repeated[0].end - repeated[0].start


def test_pvpc_reads_today_and_tomorrow(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """`price_next_day_00h` … is the evening's second day (D1 §2)."""
    state = put_state(format_fixture("pvpc"))

    rows = normalise_row(formats.build("pvpc"), state, site_currency="EUR", source_tz=MADRID)

    assert rows[-1].end.isoformat() == "2026-09-20T22:00:00+00:00"


# --------------------------------------------------------------------------- #
# the rows that read a unit off the entity, and the ones that cannot
# --------------------------------------------------------------------------- #


#: What a `<currency>/MWh` sensor's prices are divided by (D1 §5.2).
PER_MWH = 1000


@pytest.mark.parametrize(
    ("key", "unit"),
    [("entsoe", "EUR/MWh"), ("nordpool_core", "NOK/MWh")],
    ids=["entsoe", "nordpool_core"],
)
def test_a_row_scales_from_the_unit_the_entity_declares(
    key: str,
    unit: str,
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """`unit_of_measurement` is read on every parse, not configured (D-0085)."""
    per_kwh = format_fixture(key)
    per_mwh = format_fixture(key)
    per_mwh["attributes"]["unit_of_measurement"] = unit

    site = per_kwh["attributes"]["unit_of_measurement"].split("/")[0]
    kwh_rows = normalise_row(
        formats.build(key), put_state(per_kwh), site_currency=site, source_tz=AMSTERDAM
    )
    mwh_rows = normalise_row(
        formats.build(key), put_state(per_mwh), site_currency=site, source_tz=AMSTERDAM
    )

    assert [slot.value for slot in mwh_rows] == [slot.value / PER_MWH for slot in kwh_rows]


def test_energidataservice_reads_its_own_unit_and_cent_attributes(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """`unit` and `use_cent` are the row's own unit attributes (D1 §2)."""
    fixture = format_fixture("energidataservice")
    fixture["attributes"]["use_cent"] = True
    fixture["attributes"]["unit"] = "MWh"
    published = Decimal(str(fixture["attributes"]["raw_today"][0]["price"]))

    rows = normalise_row(
        formats.build("energidataservice"), put_state(fixture), site_currency="DKK", source_tz=OSLO
    )

    assert rows[0].value == published / 1000 / 100


def test_tge_takes_its_currency_from_configuration(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
) -> None:
    """A unit of `zł/MWh` names no ISO currency, so the flow supplies one."""
    adapter = formats.build("tge", {"currency": "PLN"})

    parsed = adapter.parse(put_state(format_fixture("tge")))

    assert parsed.currency == "PLN"


def test_comed_publishes_one_hour_and_no_forecast(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """The hour average is one slot in cents; the forecaster fills the rest."""
    fixture = format_fixture("comed")

    rows = normalise_row(
        formats.build("comed"), put_state(fixture), site_currency="USD", source_tz=CHICAGO
    )

    assert len(rows) == 1
    assert rows[0].value == Decimal(str(fixture["state"])) / 100
    assert rows[0].start.isoformat() == "2026-09-19T11:00:00+00:00"
    assert rows[0].end.isoformat() == "2026-09-19T12:00:00+00:00"


def test_nordpool_core_takes_its_slot_length_from_configuration(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """An hourly market's current price is an hour, a 15-minute MTU is fifteen."""
    rows = normalise_row(
        formats.build("nordpool_core", {"slot_minutes": 60}),
        put_state(format_fixture("nordpool_core")),
        site_currency="NOK",
        source_tz=OSLO,
    )

    assert len(rows) == 1
    assert rows[0].start.isoformat() == "2026-09-19T11:00:00+00:00"
    assert rows[0].end.isoformat() == "2026-09-19T12:00:00+00:00"


def test_hourly_attributes_reads_only_the_prefixes_it_is_given(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """Without a tomorrow prefix the row is one day long (D1 §2, last-but-one)."""
    adapter = formats.build("hourly_attributes", {"currency": "NOK", "today_prefix": "rate_"})

    rows = normalise_row(
        adapter, put_state(format_fixture("hourly_attributes")), site_currency="NOK", source_tz=OSLO
    )

    assert len(rows) == 24
    assert rows[0].start.isoformat() == "2026-09-18T22:00:00+00:00"


def test_amber_snaps_the_nem_second_off_its_interval(
    format_fixture: Callable[[str], dict[str, Any]],
    put_state: Callable[[dict[str, Any]], State],
    normalise_row: Callable[..., tuple[RawSlot, ...]],
) -> None:
    """The NEM opens an interval at:00:01; a slot is still 30 minutes (INV-7)."""
    fixture = format_fixture("amber")
    assert fixture["attributes"]["forecasts"][0]["start_time"].endswith(":01+10:00")

    rows = normalise_row(
        formats.build("amber"), put_state(fixture), site_currency="AUD", source_tz=SYDNEY
    )

    assert {int((slot.end - slot.start).total_seconds()) for slot in rows} == {30 * 60}
    assert gaps(rows) == ()
