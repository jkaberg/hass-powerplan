"""Registry schemas in, `vol.Schema` out (D8 §5.4); the controls of D8 §5.15.

D1's `Field`/`FieldKind` and D4's `Question` are the same idea - a registered
extension describing its own options so the flow renders from the registry and
nothing switches on a key (D1 §6, `design/DECISIONS.md` D-0037). This module owns
the `Field` half and the controls every flow shares: the fuse select, the
half-hour select, the duration, the percent, the kilowatt.

Three rules carry the money:

* a `MONEY` field, and any field whose registry default is a `Decimal`, crosses
  `entry.data` as a **decimal string**. A config entry is serialised with orjson,
  which cannot write a `Decimal` at all, and a float would turn Tensio's 0.3604
  into something that is not 0.3604 (HLD §7.2, D-0123).
* a price per kWh is **shown** in the currency's minor unit - øre, öre, cent -
  as on the bill, and a fraction as a percent, and **stored** as it always was:
  major units and fractions, so `entry.data` does not change (review CTL-2, CTL-3).
* nothing is clamped and nothing is bounded below zero: a price, an offset and a
  threshold may all be negative (INV-51).

A list of records - a time-of-use table, a tier table, day-type rates - is a form
list (`object` selector with `fields`), never the YAML editor (review CTL-6,
HUB-9). Each row is converted to the stored shape `from_options` already reads,
so an entry made before WP U.2 renders pre-filled and saves unchanged. The nested
selectors are written as plain mappings, not `Selector` instances: Home Assistant
2026.3 (the floor line) validates an object field with `selector(config)`, which
takes only a mapping - the fix that accepts an instance landed in 2026.6
(core PR #170453) - so a mapping is the one form both lines accept (D-0391).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    DurationSelector,
    DurationSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    ObjectSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from custom_components.powerplan.const import SECTION_ADVANCED
from custom_components.powerplan.core.pricing import FieldKind, modifiers
from custom_components.powerplan.core.tariffs.model import HolidayMode

from .text import minor_unit

if TYPE_CHECKING:
    from homeassistant.helpers.selector import Selector

    from custom_components.powerplan.core.pricing import Field, Schema

__all__ = [
    "CIRCUIT_FUSE_SIZES",
    "DONT_KNOW",
    "MAIN_FUSE_SIZES",
    "amps_of",
    "as_duration",
    "duration_selector",
    "fuse_selector",
    "half_hours",
    "jsonable",
    "kw_selector",
    "percent_selector",
    "render",
    "rows_of",
    "seconds_of",
    "store_value",
    "stored_of",
    "time_selector",
    "value_of",
]

# --------------------------------------------------------------------------- #
# The shared controls (D8 §5.15 "Controls")
# --------------------------------------------------------------------------- #

#: D3 §6's main fuses, extended upwards: a North-American service is rated
#: 100–400 A and D3 §6's own default for US is 200 A (`design/DECISIONS.md` D-0124).
MAIN_FUSE_SIZES: Final = (
    "16", "20", "25", "32", "35", "40", "50", "63",
    "80", "100", "125", "150", "200", "250", "320", "400",
)  # fmt: skip
#: A circuit's breaker: the sizes a house's own distribution board carries.
CIRCUIT_FUSE_SIZES: Final = ("6", "10", "13", "16", "20", "25", "32", "40", "50", "63")
#: "Vet ikke" - the country's default is taken and the review names it as assumed.
DONT_KNOW: Final = "dont_know"
_MINUTES_PER_HOUR: Final = 60
_HOURS_PER_DAY: Final = 24
_PERCENT: Final = Decimal(100)


def fuse_selector(sizes: Sequence[str], *, dont_know: bool = False) -> SelectSelector:
    """Return a fuse as a pick of standard sizes, "63 A", with "Annet…" typed in (CTL-1).

    `custom_value` is what lets the household type a size the list does not
    have; the field is `vol.Required` with its default wherever it is used, so
    Home Assistant draws no clear button on it.
    """
    options = [*sizes, DONT_KNOW] if dont_know else list(sizes)
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="fuse",
            custom_value=True,
            sort=False,
        )
    )


def amps_of(value: Any) -> float | None:
    """Return a fuse answer in amps - `"63"`, a typed `"45 A"` or `"45,5"` - or `None` (CTL-1)."""
    text = str(value).strip().upper().removesuffix("A").strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def half_hours(*keep: str | None, end: bool = False) -> list[str]:
    """Return `00:00`…`23:30` (and `24:00` for an end), plus any stored time not on the half hour.

    Home Assistant's `time` selector has no option to hide seconds (D8 §5.15 H8),
    so a time of day is a select that cannot be mistyped (review CTL-7). A value
    stored before WP U.2 that is not on the half hour stays offered, in order.
    """
    first = 1 if end else 0
    last = _HOURS_PER_DAY * 2 + (1 if end else 0)
    times = [f"{slot // 2:02d}:{(slot % 2) * 30:02d}" for slot in range(first, last)]
    for value in keep:
        clock = _hhmm(value)
        if clock and clock not in times:
            times.append(clock)
    return sorted(times)


def _hhmm(value: Any) -> str | None:
    """`"07:00:00"` or `time(7)` → `"07:00"`; nothing → `None`."""
    if value is None or value == "":
        return None
    if hasattr(value, "strftime"):
        return str(value.strftime("%H:%M"))
    text = str(value)
    return text[:5] if len(text) >= 5 and text[2] == ":" else text  # noqa: PLR2004 - HH:MM


def time_selector(*keep: str | None, end: bool = False) -> SelectSelector:
    """Return a time of day as a half-hour select (CTL-7)."""
    return SelectSelector(
        SelectSelectorConfig(
            options=half_hours(*keep, end=end), mode=SelectSelectorMode.DROPDOWN, sort=False
        )
    )


def duration_selector() -> DurationSelector:
    """Return an interval as hours and minutes; every default is a whole minute (CTL-8)."""
    return DurationSelector(DurationSelectorConfig(enable_second=False))


def as_duration(seconds: float | None) -> dict[str, int] | None:
    """Return seconds as the duration selector's `{hours, minutes, seconds}`."""
    if seconds is None:
        return None
    whole = round(float(seconds))
    return {
        "hours": whole // 3600,
        "minutes": (whole % 3600) // _MINUTES_PER_HOUR,
        "seconds": whole % _MINUTES_PER_HOUR,
    }


def seconds_of(value: Any) -> float | None:
    """Return a duration selector's answer - or a bare number of seconds - in seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        return float(
            float(value.get("days", 0)) * 86400
            + float(value.get("hours", 0)) * 3600
            + float(value.get("minutes", 0)) * 60
            + float(value.get("seconds", 0))
            + float(value.get("milliseconds", 0)) / 1000
        )
    return float(value)


def percent_selector(
    *, low: float = 0.0, high: float = 100.0, slider: bool = True
) -> NumberSelector:
    """Return a share as a percent: a slider 0–100 step 1, or a box for a rate (CTL-2)."""
    config = NumberSelectorConfig(
        min=low,
        max=high,
        step=1 if slider else "any",
        unit_of_measurement="%",
        mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
    )
    return NumberSelector(config)


def kw_selector(low_w: float, high_w: float) -> NumberSelector:
    """Return a power in kW, step 0.1, over a range given in watts (CTL-15)."""
    low, high = low_w / 1000.0, high_w / 1000.0
    return NumberSelector(
        NumberSelectorConfig(
            min=low,
            max=high,
            step=0.1 if (low * 10).is_integer() else "any",
            unit_of_measurement="kW",
            mode=NumberSelectorMode.BOX,
        )
    )


# --------------------------------------------------------------------------- #
# Money and fractions: shown one way, stored the other (CTL-2, CTL-3)
# --------------------------------------------------------------------------- #


def jsonable(value: Any) -> Any:
    """Return `value` with every `Decimal` in it as an exact decimal string.

    A config entry is written with orjson, which refuses a `Decimal` outright, and
    a preset's own numbers arrive as `Decimal`s nested inside lists and mappings -
    a time-of-use table's prices, a tier's bands. Converting to `str` rather than
    to `float` is the whole point: 0.3604 is a price on an invoice, not a binary
    fraction (HLD §7.2, D-0123).
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _per_kwh_scale(unit: str | None, currency: str | None) -> bool:
    """Whether a price per kWh is shown in the currency's minor unit."""
    return unit == "per_kwh" and currency is not None and minor_unit(currency) is not None


def _to_shown(value: Any, *, scaled: bool) -> float | None:
    """Return a stored decimal as the form shows it: ×100 when scaled, as a float."""
    if value is None:
        return None
    number = Decimal(str(value))
    return float(number * _PERCENT) if scaled else float(number)


def _to_stored(value: Any, *, scaled: bool) -> Decimal | None:
    """Return a shown number as the decimal stored: ÷100 when scaled."""
    if value is None or value == "":
        return None
    number = Decimal(str(value))
    return number / _PERCENT if scaled else number


def _price_unit(currency: str | None) -> str | None:
    """Return the unit a price per kWh is shown in: `øre/kWh`, or `<CUR>/kWh`."""
    if currency is None:
        return None
    minor = minor_unit(currency)
    return f"{minor}/kWh" if minor else f"{currency}/kWh"


def price_selector(currency: str | None) -> NumberSelector:
    """Return a price per kWh as a box in the currency's minor unit (CTL-3)."""
    config = NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
    unit = _price_unit(currency)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def price_shown(value: Any, currency: str | None) -> float | None:
    """Return a stored price per kWh (major units) as the form shows it."""
    return _to_shown(value, scaled=_per_kwh_scale("per_kwh", currency))


def price_stored(value: Any, currency: str | None) -> Decimal | None:
    """Return a shown price per kWh as the decimal stored, in major units."""
    return _to_stored(value, scaled=_per_kwh_scale("per_kwh", currency))


# --------------------------------------------------------------------------- #
# Form lists (CTL-6, HUB-9)
# --------------------------------------------------------------------------- #


def _field(selector: Mapping[str, Any], *, required: bool = False) -> dict[str, Any]:
    return {"selector": dict(selector), "required": required}


def _select(options: Sequence[str], *, key: str | None = None, multiple: bool = False) -> Any:
    config: dict[str, Any] = {
        "options": list(options),
        "mode": "dropdown",
        "sort": False,
        "multiple": multiple,
    }
    if key is not None:
        config["translation_key"] = key
    return {"select": config}


def _number(unit: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"mode": "box", "step": "any"}
    if unit is not None:
        config["unit_of_measurement"] = unit
    return {"number": config}


_WEEKDAYS: Final = tuple(str(day) for day in range(7))
_MONTHS: Final = tuple(str(month) for month in range(1, 13))
_HOLIDAYS: Final = tuple(mode.value for mode in HolidayMode)


def _minute(clock: str) -> int:
    hour, minute = (int(part) for part in clock.split(":", 1))
    return hour * _MINUTES_PER_HOUR + minute


def _clock(minute: int) -> str:
    return f"{minute // _MINUTES_PER_HOUR:02d}:{minute % _MINUTES_PER_HOUR:02d}"


@dataclass(frozen=True, slots=True)
class _Rows:
    """One list of records: its form row, and the two conversions (CTL-6)."""

    fields: Callable[[str | None, Sequence[Mapping[str, Any]]], dict[str, Any]]
    label_field: str
    to_rows: Callable[[Sequence[Any], str | None], list[dict[str, Any]]]
    from_rows: Callable[[Sequence[Mapping[str, Any]], str | None], list[dict[str, Any]]]


def _period_fields(currency: str | None, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    starts = [row.get("start") for row in rows]
    ends = [row.get("end") for row in rows]
    return {
        "start": _field(_select(half_hours(*starts))),
        "end": _field(_select(half_hours(*ends, end=True))),
        "price": _field(_number(_price_unit(currency)), required=True),
        "weekdays": _field(_select(_WEEKDAYS, key="weekday", multiple=True)),
        "months": _field(_select(_MONTHS, key="month", multiple=True)),
        "holidays": _field(_select(_HOLIDAYS, key="holidays")),
    }


def _periods_to_rows(stored: Sequence[Any], currency: str | None) -> list[dict[str, Any]]:
    """Return stored periods - `{"when": {…}, "price"}` or a preset's flat record - as rows.

    A period with two hour ranges (night: 22–24 and 00–06) is two rows with the
    same price and days; `_periods_from_rows` joins them again.
    """
    rows: list[dict[str, Any]] = []
    for period in stored:
        when = period.get("when")
        source: Mapping[str, Any] = when if isinstance(when, Mapping) else period
        base: dict[str, Any] = {"price": price_shown(period["price"], currency)}
        if source.get("weekdays") is not None:
            base["weekdays"] = [str(day) for day in source["weekdays"]]
        if source.get("months") is not None:
            base["months"] = [str(month) for month in source["months"]]
        if source.get("holidays") not in (None, HolidayMode.IGNORE.value):
            base["holidays"] = str(source["holidays"])
        hours = source.get("hours")
        if not hours:
            rows.append(base)
            continue
        rows.extend(
            {**base, "start": _clock(int(start)), "end": _clock(int(end))} for start, end in hours
        )
    return rows


def _period_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("price")),
        tuple(row.get("weekdays") or ()),
        tuple(row.get("months") or ()),
        row.get("holidays") or HolidayMode.IGNORE.value,
    )


def _periods_from_rows(
    rows: Sequence[Mapping[str, Any]], currency: str | None
) -> list[dict[str, Any]]:
    """Form rows as the stored `{"when": {…}, "price": "…"}`, adjacent ranges joined."""
    periods: list[dict[str, Any]] = []
    previous: tuple[Any, ...] | None = None
    for row in rows:
        start, end = row.get("start"), row.get("end")
        span = None
        if start or end:
            span = [_minute(str(start or "00:00")), _minute(str(end or "24:00"))]
        key = _period_key(row)
        if span is not None and key == previous and periods[-1]["when"].get("hours"):
            periods[-1]["when"]["hours"].append(span)
            continue
        when: dict[str, Any] = {}
        if span is not None:
            when["hours"] = [span]
        if row.get("weekdays"):
            when["weekdays"] = [int(day) for day in row["weekdays"]]
        if row.get("months"):
            when["months"] = [int(month) for month in row["months"]]
        if row.get("holidays") not in (None, "", HolidayMode.IGNORE.value):
            when["holidays"] = str(row["holidays"])
        price = price_stored(row["price"], currency)
        periods.append({"when": when or None, "price": str(price)})
        previous = key if span is not None else None
    return periods


def _tier_fields(currency: str | None, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    del rows
    return {
        "upto_kwh": _field(_number("kWh")),
        "price": _field(_number(_price_unit(currency)), required=True),
    }


def _tiers_to_rows(stored: Sequence[Any], currency: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tier in stored:
        row: dict[str, Any] = {"price": price_shown(tier["price"], currency)}
        if tier.get("upto_kwh") is not None:
            row["upto_kwh"] = float(tier["upto_kwh"])
        rows.append(row)
    return rows


def _tiers_from_rows(
    rows: Sequence[Mapping[str, Any]], currency: str | None
) -> list[dict[str, Any]]:
    return [
        {
            "upto_kwh": None if row.get("upto_kwh") in (None, "") else float(row["upto_kwh"]),
            "price": str(price_stored(row["price"], currency)),
        }
        for row in rows
    ]


def _rate_fields(currency: str | None, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    del rows
    return {
        "type": _field({"text": {}}, required=True),
        "price": _field(_number(_price_unit(currency))),
        "multiplier": _field(_number()),
    }


def _rates_to_rows(stored: Any, currency: str | None) -> list[dict[str, Any]]:
    """Day-type rates - `{type: {price, multiplier}}` or rows with a `type` - as form rows."""
    items = stored.items() if isinstance(stored, Mapping) else ((r["type"], r) for r in stored)
    rows: list[dict[str, Any]] = []
    for name, rate in items:
        row: dict[str, Any] = {"type": str(name)}
        if rate.get("price") is not None:
            row["price"] = price_shown(rate["price"], currency)
        if rate.get("multiplier") is not None:
            row["multiplier"] = float(Decimal(str(rate["multiplier"])))
        rows.append(row)
    return rows


def _rates_from_rows(
    rows: Sequence[Mapping[str, Any]], currency: str | None
) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    for row in rows:
        price = price_stored(row.get("price"), currency)
        multiplier = _to_stored(row.get("multiplier"), scaled=False)
        stored.append(
            {
                "type": str(row["type"]),
                "price": None if price is None else str(price),
                "multiplier": None if multiplier is None else str(multiplier),
            }
        )
    return stored


#: The lists of records the registries declare, by `<translation prefix>.<field>`.
_ROWS: Final[Mapping[str, _Rows]] = {
    "modifier_tou_schedule.periods": _Rows(
        _period_fields, "price", _periods_to_rows, _periods_from_rows
    ),
    "modifier_cumulative_tier.tiers": _Rows(
        _tier_fields, "price", _tiers_to_rows, _tiers_from_rows
    ),
    "modifier_day_type.rates": _Rows(_rate_fields, "type", _rates_to_rows, _rates_from_rows),
}


def _components() -> list[str]:
    """Every price component a modifier writes, VAT's own excepted - what VAT may apply to."""
    return sorted(
        {modifiers.entry(key).component for key in modifiers.keys()} - {"vat"}  # noqa: SIM118
    )


#: The lists of plain values, by `<translation prefix>.<field>`: a multi-select.
_CHOICES: Final[Mapping[str, tuple[Callable[[], Sequence[str]], str, Callable[[str], Any]]]] = {
    "modifier_vat.applies_to": (_components, "price_component", str),
    "modifier_levy.months": (lambda: _MONTHS, "month", int),
}


def rows_of(prefix: str, key: str) -> _Rows | None:
    """Return the form list one registry field renders as, if it is a list of records."""
    return _ROWS.get(f"{prefix}.{key}")


# --------------------------------------------------------------------------- #
# Rendering (D8 §5.4)
# --------------------------------------------------------------------------- #

#: What a translation key may be (hassfest's own rule): an option value outside it -
#: a market area such as `NO3` - is a code shown as itself, not a word to translate.
_TRANSLATABLE = re.compile(r"(?![_-])[a-z0-9_-]+(?<![_-])")


def _choice(options: tuple[str, ...], translation_key: str | None) -> Selector[Any]:
    config = SelectSelectorConfig(
        options=list(options),
        mode=SelectSelectorMode.DROPDOWN,
        sort=False,
    )
    if translation_key is not None:
        config["translation_key"] = translation_key
    return SelectSelector(config)


def _plain_number(field: Field) -> Selector[Any]:
    config = NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
    if field.unit not in (None, "fraction", "rate", "factor", "per_kwh"):
        config["unit_of_measurement"] = field.unit
    return NumberSelector(config)


def _selector(  # noqa: PLR0911 - one control per kind, as D8 §5.15 tabulates them
    field: Field, prefix: str, currency: str | None, shown: Any
) -> Selector[Any]:
    """Return the selector one registry field renders as (D8 §5.4, §5.15 controls).

    Every option of a `SELECT` field is translated under
    `selector.<prefix>_<field>` (review HUB-10), unless its values are codes
    that no translation key can spell - a Nord Pool area is `NO3` in every
    language.
    """
    path = f"{prefix}.{field.key}"
    match field.kind:
        case FieldKind.SELECT:
            options = tuple(str(option) for option in field.options)
            key = f"{prefix}_{field.key}" if field.options else None
            if not all(_TRANSLATABLE.fullmatch(option) for option in options):
                key = None
            return _choice(options, key)
        case FieldKind.MONEY if field.unit == "per_kwh":
            return price_selector(currency)
        case FieldKind.NUMBER if field.unit in ("fraction", "rate"):
            return percent_selector(slider=field.unit == "fraction")
        case FieldKind.MONEY | FieldKind.NUMBER:
            return _plain_number(field)
        case FieldKind.BOOL:
            return BooleanSelector()
        case FieldKind.TIME:
            return time_selector(shown)
        case FieldKind.ENTITY:
            return EntitySelector(EntitySelectorConfig())
        case FieldKind.LIST if (rows := _ROWS.get(path)) is not None:
            return ObjectSelector(
                ObjectSelectorConfig(
                    fields=rows.fields(currency, shown or ()),
                    multiple=True,
                    label_field=rows.label_field,
                    translation_key=f"{prefix}_{field.key}",
                )
            )
        case FieldKind.LIST if path in _CHOICES:
            options_fn, translation_key, _to_value = _CHOICES[path]
            return SelectSelector(
                SelectSelectorConfig(
                    options=list(options_fn()),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=translation_key,
                    sort=False,
                )
            )
        case _:
            return TextSelector(TextSelectorConfig())


def _is_decimal(field: Field) -> bool:
    """Return whether this field's value must survive as an exact decimal."""
    return field.kind is FieldKind.MONEY or isinstance(field.default, Decimal)


def shown_of(  # noqa: PLR0911 - one form per kind
    field: Field, value: Any, *, prefix: str = "", currency: str | None = None
) -> Any:
    """Return a stored (or default) value in the form its control shows (CTL-2, 3, 6)."""
    if value is None:
        return None
    path = f"{prefix}.{field.key}"
    if field.kind is FieldKind.LIST:
        if (rows := _ROWS.get(path)) is not None:
            return rows.to_rows(value, currency)
        if path in _CHOICES:
            return [str(item) for item in value]
        return value
    if field.kind is FieldKind.MONEY:
        return _to_shown(value, scaled=_per_kwh_scale(field.unit, currency))
    if field.kind is FieldKind.NUMBER and field.unit in ("fraction", "rate"):
        return _to_shown(value, scaled=True)
    if field.kind is FieldKind.TIME:
        return _hhmm(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def stored_of(  # noqa: PLR0911 - one form per kind
    field: Field, value: Any, *, prefix: str = "", currency: str | None = None
) -> Any:
    """Return a shown value as `entry.data` stores it (the inverse of `shown_of`)."""
    if value is None or value == "":
        return None
    path = f"{prefix}.{field.key}"
    if field.kind is FieldKind.LIST:
        if (rows := _ROWS.get(path)) is not None:
            return rows.from_rows(value, currency)
        if (choice := _CHOICES.get(path)) is not None:
            return [choice[2](item) for item in value] or None
        return value
    if field.kind is FieldKind.MONEY:
        return _to_stored(value, scaled=_per_kwh_scale(field.unit, currency))
    if field.kind is FieldKind.NUMBER and field.unit in ("fraction", "rate"):
        return _to_stored(value, scaled=True)
    if field.kind is FieldKind.TIME:
        return _hhmm(value)
    return value


def marker(key: str, default: Any) -> Any:
    """Return the schema marker for one field: optional, with its default.

    Every answer has a default (HLD §7.9 (2)), and a field with no default is
    one the household may leave empty - an unbound meter role, an export sensor
    it does not have. The one exception is `render()`'s: a registry field that is
    required and has no default (D8 §9 21 (a)).
    """
    return vol.Optional(key, default=default) if default is not None else vol.Optional(key)


def advanced_section(fields: Mapping[Any, Any]) -> Any:
    """Wrap `fields` in the collapsed advanced section (INV-65, D-0129)."""
    return section(vol.Schema(dict(fields)), {"collapsed": True})


def render(
    schema: Schema,
    *,
    translation_prefix: str,
    values: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Selector[Any]] | None = None,
    currency: str | None = None,
    suggested: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Render a registry `Schema` as a form (D8 §5.4, §5.15).

    An advanced field goes into a collapsed `advanced` section rather than being
    dropped: it is pre-filled, never required, and there to be found (INV-65).
    A field that is **required and has no default** is `vol.Required` with none -
    Norgespris's price is one, and `render()` making it optional is how the
    review's Norgespris took an empty price (review §10, D8 §9 21 (a)). A value
    derived at run time is shown as a `suggested_value` (review HUB-19):
    `suggested` maps the field to it. `overrides` replaces one field's selector
    where the registry's kind is not specific enough - a config entry id (D-0122).
    """
    given = values or {}
    chosen = overrides or {}
    hints = suggested or {}
    fields: dict[Any, Any] = {}
    hidden: dict[Any, Any] = {}
    for field in schema:
        stored = given.get(field.key)
        shown = shown_of(
            field,
            field.default if stored is None else stored,
            prefix=translation_prefix,
            currency=currency,
        )
        selector = chosen.get(field.key) or _selector(field, translation_prefix, currency, shown)
        if stored is None and field.key in hints:
            key: Any = vol.Optional(field.key, description={"suggested_value": hints[field.key]})
        elif shown is None and field.required and not field.advanced:
            key = vol.Required(field.key)
        else:
            key = marker(field.key, shown)
        (hidden if field.advanced else fields)[key] = selector
    if hidden:
        fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
    return vol.Schema(fields)


def missing_required(schema: Schema, answers: Mapping[str, Any]) -> str | None:
    """Return the first required field left empty - an empty list included - or `None`."""
    for field in schema:
        if field.required and field.default is None and answers.get(field.key) in (None, "", []):
            return field.key
    return None


def implausible_price(
    schema: Schema, answers: Mapping[str, Any], currency: str | None
) -> str | None:
    """Return a per-kWh price typed as kroner into an øre box, or `None` (INV-49).

    The box is in the minor unit (CTL-3); "0,4" there is 0.4 øre, which no
    agreement charges. A site stored Norgespris that way once.
    """
    for field in schema:
        if field.kind is not FieldKind.MONEY or not _per_kwh_scale(field.unit, currency):
            continue
        if price_implausible(answers.get(field.key)):
            return field.key
    return None


def price_implausible(value: Any) -> bool:
    """Whether a price shown in øre per kWh is above zero and below one øre."""
    try:
        amount = float(value)
    except TypeError, ValueError:
        return False
    return 0 < abs(amount) < 1


def store_value(field: Field, value: Any) -> Any:
    """Return `value` in the form `entry.data` holds it (D-0123)."""
    if value is None:
        return None
    if _is_decimal(field) and not isinstance(value, (Mapping, list, tuple)):
        return str(Decimal(str(value)))
    return jsonable(value)


def value_of(
    schema: Schema,
    answers: Mapping[str, Any],
    *,
    prefix: str = "",
    currency: str | None = None,
) -> dict[str, Any]:
    """Return every answered option of one registry entry, ready to store.

    What the form showed in minor units, percents or rows is converted back to
    the stored shape first (`stored_of`), so `entry.data` is what it always was.
    """
    stored: dict[str, Any] = {}
    for field in schema:
        if field.key in answers:
            value = stored_of(field, answers[field.key], prefix=prefix, currency=currency)
            stored[field.key] = store_value(field, value)
        elif field.default is not None:
            stored[field.key] = store_value(field, field.default)
    return stored
