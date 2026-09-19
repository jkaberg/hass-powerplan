"""The format protocols: one adapter per row of D1 §2's format table.

An adapter's whole job is to say where the prices are in one integration's output
and what unit they are in. It does not normalise, does not know the site's
currency and does not know what day is being asked for:
`providers/prices/base.normalise` and the source do that once, for every row.

Two kinds of row, one registry (`FormatKind`, `design/DECISIONS.md` D-0100):

- `ATTRIBUTES` - the prices are on an entity's state or attributes. The adapter
  is an `EntityFormat` with one method, `parse(state)`, and `EntitySource` reads
  the state (INV-3).
- `ACTION` - the integration keeps its prices behind a response action. The
  adapter is an `ActionFormat`; `ActionSource` calls it and normalises the result
  the same way.

An adapter reads what it is handed and nothing else. A payload it no longer
recognises is a `SourceParseError` - never a guess, never a silent empty list:
prices are what every decision downstream is made of, and a source that failed
must fail loudly enough for the forecaster to take over (INV-5) and for the
repair issue to name it (D1 §8).

A price that is absent or `None` is the one exception, and it is not a failure: it
is an interval the integration could not price, so it becomes a hole for the
forecaster to fill. A zero would be a lie about a market that never opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol, runtime_checkable

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Field, FieldKind
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, tzinfo

    from homeassistant.core import HomeAssistant, State

    from custom_components.powerplan.core.pricing import Publication, Schema

#: `unit_of_measurement` spellings for the energy unit half of `<CUR>/<unit>`.
ENERGY_UNITS: Final[dict[str, EnergyUnit]] = {
    "kwh": EnergyUnit.KWH,
    "mwh": EnergyUnit.MWH,
}

#: Currency symbols the WP4.7 rows write in `unit_of_measurement` instead of an
#: ISO code, each as the integration's own source spells it: `€/kWh` and
#: `£/kWh` (`epex_spot`'s `localization.py`), `Kč/kWh` (`cz_energy_spot_prices`),
#: `kr/kWh` is not here - it names three currencies.
CURRENCY_SYMBOLS: Final[dict[str, str]] = {"€": "EUR", "£": "GBP", "Kč": "CZK"}

#: How long an ISO 4217 code is: the test that tells `EUR/kWh` from `zl/MWh`.
ISO_CURRENCY_LENGTH: Final = 3

#: The slot lengths a row that publishes one price at a time may be told to
#: assume: Nord Pool's 15-minute MTU, an hourly market, a half-hourly one.
SLOT_MINUTES: Final = (5, 15, 30, 60)


def slot_minutes_field(default: int) -> Field:
    """Return the schema field those rows render for it (D1 §6, Advanced)."""
    return Field(
        key="slot_minutes",
        kind=FieldKind.SELECT,
        default=str(default),
        options=tuple(str(minutes) for minutes in SLOT_MINUTES),
        advanced=True,
    )


@dataclass(frozen=True, slots=True)
class EntityFacts:
    """What the prices step knows about the entity the household picked (D1 §6).

    Read from the entity and device registries and the entity's unit, so the rows
    that need a config entry, a currency or a home are told them, not asked.
    `entry_devices` is `(name, model)` for every device of the entity's config
    entry - how a row tells a one-home account from a two-home one.
    """

    entity_id: str
    config_entry_id: str | None = None
    currency: str | None = None
    device_name: str | None = None
    entry_devices: tuple[tuple[str | None, str | None], ...] = ()


class FormatKind(StrEnum):
    """Where a row's prices come from (D1 §2, D-0100)."""

    ATTRIBUTES = "attributes"
    ACTION = "action"


@dataclass(frozen=True, slots=True)
class ParsedPrices:
    """What one adapter found in one payload (D1 §2).

    The unit travels with the intervals because several integrations put it in an
    attribute the user can change (the HACS Nord Pool sensor's `price_type`), so
    it is a property of the reading, not of the configuration.
    """

    intervals: tuple[Interval, ...]
    currency: str
    energy: EnergyUnit
    magnitude: Magnitude


@runtime_checkable
class EntityFormat(Protocol):
    """How to read prices out of one integration's price entity (D1 §2).

    `platform` is the entity registry platform the config flow detects the format
    from; `None` means the adapter fits any entity and has to be chosen by hand
    (`generic_list`, `hourly_attributes`).
    """

    key: ClassVar[str]
    platform: ClassVar[str | None]
    schema: ClassVar[Schema]
    kind: ClassVar[FormatKind]

    def parse(self, state: State) -> ParsedPrices:
        """Return the prices in `state`. Raises `SourceParseError`."""
        ...


@runtime_checkable
class ActionFormat(Protocol):
    """How to read prices out of one integration's response action (D-0100).

    The action call itself goes through `providers/prices/action.py`, which is the
    one file on INV-3's allowlist for these rows, and `ActionSource` normalises
    what comes back. An adapter here knows the action's name, the data it takes
    and the shape it answers with - nothing else.
    """

    key: ClassVar[str]
    platform: ClassVar[str | None]
    schema: ClassVar[Schema]
    kind: ClassVar[FormatKind]

    def publication(self) -> Publication | None:
        """When the market publishes tomorrow, or `None` for a continuous source."""
        ...

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return the currency, energy unit and magnitude the action answers in."""
        ...

    async def fetch(self, hass: HomeAssistant, day: date, *, tz: tzinfo) -> ParsedPrices:
        """Call the action for the local day `day`. Raises `SourceError`."""
        ...


# --------------------------------------------------------------------------- #
# what every adapter needs: a list, a boundary, a price
# --------------------------------------------------------------------------- #


def listed(raw: Any, *, where: str) -> Sequence[Any]:
    """Return `raw` as a sequence of rows, or fail loudly (D1 §8)."""
    if not isinstance(raw, list | tuple):
        raise SourceParseError(f"{where} is a {type(raw).__name__}, not a list of slots")
    return raw


def moment(raw: Any, *, where: str) -> datetime:
    """Parse one slot boundary: a `datetime` live, an ISO string from a dump.

    Naive timestamps are left naive on purpose - `normalise` localises them with
    the *source's* declared timezone, which is the only thing that knows which
    zone a bare local time was written in (D1 §5.2).
    """
    if isinstance(raw, datetime):
        return raw
    parsed = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise SourceParseError(f"{where} is {raw!r}, not a slot boundary")
    return parsed


def price(raw: Any, *, where: str) -> Decimal:
    """Convert one price to `Decimal` through `str`, never through binary float."""
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as err:
        raise SourceParseError(f"{where} is {raw!r}, not a price") from err


def interval_rows(
    raw: Any,
    *,
    where: str,
    start_key: str,
    value_key: str,
    end_key: str | None = None,
) -> list[Interval]:
    """Read a list of slot dicts into `Interval`s (D1 §2, D-0088).

    `end_key` is left out by the integrations that publish only starts; the slot
    then ends where the next one begins, never at an assumed 60 minutes (INV-7).
    `value_key` may be a dotted path (`price_tax_included.amount`, `dotted`).
    A row whose price is absent or `None` is skipped: the integration could not
    price that interval, and a hole is the truth about it.
    """
    out: list[Interval] = []
    for index, row in enumerate(listed(raw, where=where)):
        if not isinstance(row, dict):
            raise SourceParseError(f"{where}[{index}] is a {type(row).__name__}, not a slot")
        value = dotted(row, value_key)
        if value is None:
            continue
        raw_end = row.get(end_key) if end_key else None
        out.append(
            Interval(
                start=moment(row.get(start_key), where=f"{where}[{index}][{start_key!r}]"),
                end=(
                    moment(raw_end, where=f"{where}[{index}][{end_key!r}]")
                    if raw_end is not None
                    else None
                ),
                value=price(value, where=f"{where}[{index}][{value_key!r}]"),
            )
        )
    return out


def attribute_intervals(
    state: State,
    attributes: Sequence[str],
    *,
    start_key: str,
    value_key: str,
    end_key: str | None = None,
    required: bool = True,
) -> list[Interval]:
    """Read one or more list-valued attributes into one series of `Interval`s.

    Most rows of D1 §2 publish today and tomorrow as two attributes of the same
    shape; they are read in order and handed back as one series, because a curve
    does not care which attribute a slot came from. An attribute that is simply
    absent is nothing known yet - tomorrow before publication - but an entity with
    *none* of them is an entity this adapter cannot read, and that is a
    `SourceParseError` (D1 §8).
    """
    out: list[Interval] = []
    found = False
    for attribute in attributes:
        rows = state.attributes.get(attribute)
        if rows is None:
            continue
        found = True
        out.extend(
            interval_rows(
                rows,
                where=f"{state.entity_id}.{attribute}",
                start_key=start_key,
                end_key=end_key,
                value_key=value_key,
            )
        )
    if not found and required:
        raise SourceParseError(
            f"{state.entity_id} has none of {list(attributes)}; it has {sorted(state.attributes)}"
        )
    return out


def state_interval(state: State, *, slot_minutes: int) -> Interval:
    """Read a row whose price is the state itself (D1 §2: `nordpool_core`, `comed`).

    Two integrations publish the price of the interval they are in and no forecast
    at all. The interval is therefore the one the entity was last written in,
    snapped back to the market's own grid - `last_reported`, because the price of
    two consecutive slots can be identical and `last_changed` would then be
    stale. The forecaster fills everything else (INV-5).
    """
    stamp = state.last_reported.astimezone(UTC)
    start = stamp.replace(
        minute=stamp.minute - stamp.minute % slot_minutes, second=0, microsecond=0
    )
    return Interval(
        start=start,
        end=start + timedelta(minutes=slot_minutes),
        value=price(state.state, where=f"{state.entity_id}'s state"),
    )


def declared_unit(state: State) -> tuple[str, EnergyUnit] | None:
    """Return the `(currency, energy unit)` the entity declares, if it declares one.

    `unit_of_measurement` is `EUR/kWh`, `NOK/MWh`, `¢/kWh`, `zł/MWh` - a currency
    and an energy unit, where the currency is only usable if it is an ISO code.
    Reading it on every parse rather than storing it is what keeps a user who
    switches their sensor from MWh to kWh from silently planning on prices a
    thousand times too high (`design/DECISIONS.md` D-0085).
    """
    unit = state.attributes.get("unit_of_measurement")
    if not isinstance(unit, str) or unit.count("/") != 1:
        return None
    currency, _, energy = unit.partition("/")
    known = ENERGY_UNITS.get(energy.strip().lower())
    iso = len(currency) == ISO_CURRENCY_LENGTH and currency.isascii() and currency.isupper()
    if known is None or not iso:
        return None
    return (currency, known)


def symbol_unit(state: State) -> tuple[str, EnergyUnit] | None:
    """Return `(currency, energy unit)` from a unit written with an ISO code or a symbol.

    `declared_unit` reads `EUR/kWh`; the rows added in WP4.7 write `€/kWh` or
    `Kč/MWh`, so a symbol in `CURRENCY_SYMBOLS` is read as its code. Anything
    else is `None`, never a guess.
    """
    return declared_unit(state) or unit_currency(state.attributes.get("unit_of_measurement"))


def unit_currency(unit: Any) -> tuple[str, EnergyUnit] | None:
    """Return `(currency, energy unit)` for a unit string with a symbol (`symbol_unit`).

    Also what the prices step reads off the entity registry's `unit_of_measurement`
    (the flow reads no state, INV-3): there `EUR/kWh` is read too.
    """
    if not isinstance(unit, str) or unit.count("/") != 1:
        return None
    symbol, _, energy = unit.partition("/")
    symbol = symbol.strip()
    iso = len(symbol) == ISO_CURRENCY_LENGTH and symbol.isascii() and symbol.isupper()
    currency = symbol if iso else CURRENCY_SYMBOLS.get(symbol)
    known = ENERGY_UNITS.get(energy.strip().lower())
    if currency is None or known is None:
        return None
    return (currency, known)


def dotted(row: dict[str, Any], path: str) -> Any:
    """Return `row[a][b]…` for the path `a.b…`, or `None` where a step is missing.

    `zonneplan_one` nests its price (`price_tax_included.amount`), and
    `generic_list`'s value key may be a path to reach payloads like it.
    A missing step is an absent price - what the caller does with that is its rule.
    """
    found: Any = row
    for step in path.split("."):
        if not isinstance(found, dict):
            return None
        found = found.get(step)
    return found


__all__ = [
    "CURRENCY_SYMBOLS",
    "SLOT_MINUTES",
    "ActionFormat",
    "EntityFacts",
    "EntityFormat",
    "FormatKind",
    "ParsedPrices",
    "attribute_intervals",
    "declared_unit",
    "dotted",
    "interval_rows",
    "listed",
    "moment",
    "price",
    "slot_minutes_field",
    "state_interval",
    "symbol_unit",
    "unit_currency",
]
