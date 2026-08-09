"""Preset files in, grammar out - and the boundary that validates them (D2 §2, §6).

Presets are data, not code: a new DSO is a JSON file under `presets/<cc>/`, and
neither this module nor the config flow changes with it. That only holds if the
file is checked, so this is a real boundary: every field is validated against
`schema.json` before anything is built, and the semantic rules the schema cannot
express - versions strictly ascending, exactly one open-ended step, a period
change that needs a `history_policy`, a version without a source that has to say
it is assumed, a `tz` that no system knows - are checked here as well.

`schema.json` is interpreted by the small subset validator below rather than by
`jsonschema`: `core/` carries no third-party dependency, and the keywords the
schema uses are few and fixed (`design/DECISIONS.md` D-0053).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...model import Money
from ..grammar import (
    ContractedPower,
    HolidayMode,
    Linear,
    NoPeak,
    PeakTariff,
    PeriodLimit,
    Ratchet,
    Step,
    StepTable,
    TariffSpec,
    TariffVersion,
    Tiers,
    TimeFilter,
    WeightRule,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..grammar import Grammar

__all__ = [
    "EnergyRate",
    "PresetError",
    "SummaryBand",
    "TariffSummary",
    "load",
    "summarize",
    "validate",
]

HERE = Path(__file__).parent
SCHEMA_PATH = HERE / "schema.json"


class PresetError(ValueError):
    """A preset file that cannot be trusted (D2 §6, §8).

    Raised by the loader only. D8 turns it into a repair issue: the site falls
    back to the grammar copied into its store at setup, which is why a broken
    preset in a release can never move a live ceiling (INV-66, D2 §8).
    """


# --------------------------------------------------------------------------- #
# A JSON Schema subset, enough for schema.json and nothing more
# --------------------------------------------------------------------------- #


def _fail(path: str, message: str, source: str) -> None:
    where = path or "(root)"
    raise PresetError(f"{source}: {where}: {message}")


def _check(
    value: Any, schema: Mapping[str, Any], path: str, root: Mapping[str, Any], src: str
) -> None:
    if "$ref" in schema:
        _check(value, _resolve(schema["$ref"], root), path, root, src)
        return
    expected = schema.get("type")
    if expected is not None and not _is_type(value, expected):
        _fail(path, f"expected type {expected}, got {type(value).__name__}", src)
    if "enum" in schema and value not in schema["enum"]:
        _fail(path, f"{value!r} is not one of {schema['enum']}", src)
    if isinstance(value, str) and "pattern" in schema and not re.match(schema["pattern"], value):
        _fail(path, f"{value!r} does not match pattern {schema['pattern']}", src)
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(path, f"{value} is below the minimum {schema['minimum']}", src)
        if "maximum" in schema and value > schema["maximum"]:
            _fail(path, f"{value} is above the maximum {schema['maximum']}", src)
    if isinstance(value, list):
        _check_array(value, schema, path, root, src)
    if isinstance(value, dict):
        _check_object(value, schema, path, root, src)


def _check_array(
    value: list[Any], schema: Mapping[str, Any], path: str, root: Mapping[str, Any], src: str
) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        _fail(path, f"needs at least {schema['minItems']} items (minItems)", src)
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        _fail(path, f"takes at most {schema['maxItems']} items (maxItems)", src)
    for index, item in enumerate(value):
        prefix = schema.get("prefixItems")
        if prefix is not None and index < len(prefix):
            _check(item, prefix[index], f"{path}[{index}]", root, src)
        elif "items" in schema:
            _check(item, schema["items"], f"{path}[{index}]", root, src)


def _check_object(
    value: Mapping[str, Any],
    schema: Mapping[str, Any],
    path: str,
    root: Mapping[str, Any],
    src: str,
) -> None:
    properties: Mapping[str, Any] = schema.get("properties", {})
    for key in schema.get("required", ()):
        if key not in value:
            _fail(path, f"{key!r} is required", src)
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                _fail(path, f"{key!r} is not a known field (additionalProperties)", src)
    for key, item in value.items():
        if key in properties:
            _check(item, properties[key], f"{path}.{key}" if path else key, root, src)


#: The JSON Schema type keywords `schema.json` uses, and nothing else.
_JSON_TYPES: Mapping[str, Callable[[Any], bool]] = {
    "null": lambda value: value is None,
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: (
        isinstance(value, int | float | Decimal) and not isinstance(value, bool)
    ),
    "string": lambda value: isinstance(value, str),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


def _is_type(value: Any, expected: str | Sequence[str]) -> bool:
    names = [expected] if isinstance(expected, str) else list(expected)
    return any(_JSON_TYPES[name](value) for name in names)


def _resolve(ref: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    node: Any = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    resolved: Mapping[str, Any] = node
    return resolved


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _schema() -> Mapping[str, Any]:
    loaded: Mapping[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return loaded


def validate(raw: Mapping[str, Any], *, source: str = "preset") -> None:
    """Check a preset against `schema.json` and D2's semantic rules (D2 §2).

    Raises `PresetError` with the field's path. Everything it checks is something a
    community file can get wrong.
    """
    schema = _schema()
    _check(raw, schema, "", schema, source)

    zone = raw.get("tz")
    if zone is not None:
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError, ValueError:
            _fail("tz", f"{zone!r} is not a zone this system knows", source)

    versions = raw["versions"]
    previous: date | None = None
    previous_period: str | None = None
    for index, version in enumerate(versions):
        path = f"versions[{index}]"
        valid_from = date.fromisoformat(version["valid_from"])
        if previous is not None and valid_from <= previous:
            _fail(path, "valid_from must be strictly ascending (overlapping versions)", source)
        previous = valid_from

        if version.get("verified", raw.get("verified")) is None and not (
            version.get("assumed") or raw.get("assumed")
        ):
            _fail(
                path,
                "verified is null, so `assumed` must say in one sentence what was "
                "assumed and why — a number without a source is never silent",
                source,
            )

        roots = [key for key in ("peak", "contracted", "no_peak") if key in version]
        if not roots:
            _fail(path, "needs one of peak, contracted or no_peak", source)
        peak = version.get("peak")
        if peak is not None:
            _validate_peak(peak, f"{path}.peak", source, raw)
            period = peak["period"]
            if (
                previous_period is not None
                and period != previous_period
                and version.get("history_policy") is None
            ):
                _fail(
                    path,
                    f"changes period from {previous_period} to {period}; say what happens "
                    "to the history with history_policy: reset | carry",
                    source,
                )
            previous_period = period


def _validate_peak(peak: Mapping[str, Any], path: str, source: str, raw: Mapping[str, Any]) -> None:
    pricing = peak["pricing"]
    shapes = [key for key in ("steps", "linear", "tiers") if key in pricing]
    if len(shapes) != 1:
        _fail(f"{path}.pricing", "needs exactly one of steps, linear or tiers", source)
    if not raw.get("currency"):
        _fail(path, "a priced peak needs the preset's currency", source)
    if "steps" in pricing:
        _validate_open_ended(pricing["steps"], f"{path}.pricing.steps", source, index=0)
        uppers = [row[0] for row in pricing["steps"] if row[0] is not None]
        if uppers != sorted(uppers) or len(set(uppers)) != len(uppers):
            _fail(f"{path}.pricing.steps", "upper bounds must be strictly ascending", source)
    if "tiers" in pricing:
        _validate_open_ended(pricing["tiers"], f"{path}.pricing.tiers", source, index=0)
    if peak["per_period"] == "mean_top_n" and peak.get("n", 1) < 1:
        _fail(path, "mean_top_n needs n >= 1", source)


def _validate_open_ended(rows: Sequence[Any], path: str, source: str, *, index: int) -> None:
    open_ended = [position for position, row in enumerate(rows) if row[index] is None]
    if open_ended != [len(rows) - 1]:
        _fail(
            path,
            "needs exactly one open-ended band (upper bound null) and it must be last",
            source,
        )


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load(name: str) -> TariffSpec:
    """Load a shipped preset by path stem, e.g. `no/tensio` or `custom` (D2 §3).

    JSON numbers are read as `Decimal` so a price is exact from the file to the
    bill: money is never a float in this integration (HLD §7.2).
    """
    path = (HERE / f"{name}.json").resolve()
    if not path.is_file() or HERE not in path.parents:
        raise PresetError(f"no preset {name!r} under {HERE}")
    raw: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    validate(raw, source=f"{name}.json")
    return _build(raw)


def _build(raw: Mapping[str, Any]) -> TariffSpec:
    preset_id = raw["id"]
    currency = raw.get("currency", "")
    versions = tuple(_version(entry, preset_id, currency, raw) for entry in raw["versions"])
    return TariffSpec(
        id=preset_id,
        name=raw["name"],
        versions=versions,
        currency=currency,
        country=raw.get("country"),
        operator=raw.get("operator"),
        source_url=raw.get("source_url"),
        verified=raw.get("verified"),
        assumed=raw.get("assumed"),
    )


def _version(
    entry: Mapping[str, Any], preset_id: str, currency: str, raw: Mapping[str, Any]
) -> TariffVersion:
    valid_from = date.fromisoformat(entry["valid_from"])
    roots: list[Grammar] = []
    if "peak" in entry:
        roots.append(_peak(entry["peak"], currency))
    if "contracted" in entry:
        roots.append(_contracted(entry["contracted"], currency))
    if entry.get("no_peak") or not roots:
        roots.append(NoPeak())
    return TariffVersion(
        valid_from=valid_from,
        version_id=f"{preset_id}@{valid_from.isoformat()}",
        grammar=tuple(roots),
        energy_components=entry.get("energy_components", {}),
        verified=entry.get("verified", raw.get("verified")),
        assumed=entry.get("assumed", raw.get("assumed")),
        source_url=entry.get("source_url", raw.get("source_url")),
        history_policy=entry.get("history_policy"),
    )


def _peak(raw: Mapping[str, Any], currency: str) -> PeakTariff:
    pricing = raw["pricing"]
    shape: StepTable | Linear | Tiers
    if "steps" in pricing:
        shape = StepTable(
            steps=tuple(
                Step(
                    upper_kw=None if upper is None else float(upper),
                    fee_per_period=Money(Decimal(str(fee)), currency),
                    name=name,
                )
                for upper, fee, name in pricing["steps"]
            )
        )
    elif "linear" in pricing:
        linear = pricing["linear"]
        shape = Linear(
            price_per_kw=Money(Decimal(str(linear["price_per_kw"])), currency),
            free_kw=float(linear.get("free_kw", 0.0)),
            min_kw=float(linear.get("min_kw", 0.0)),
        )
    else:
        shape = Tiers(
            bands=tuple(
                (None if upto is None else float(upto), Money(Decimal(str(price)), currency))
                for upto, price in pricing["tiers"]
            )
        )
    ratchet = raw.get("ratchet")
    return PeakTariff(
        window_min=raw["window_min"],
        eligible=_filter(raw.get("eligible")),
        weights=tuple(
            WeightRule(when=_require_filter(rule["when"]), weight=float(rule["weight"]))
            for rule in raw.get("weights", ())
        ),
        per_day=raw["per_day"],
        per_period=raw["per_period"],
        period=raw["period"],
        pricing=shape,
        n=int(raw.get("n", 1)),
        distinct_days=bool(raw.get("distinct_days", True)),
        rolling_months=int(raw.get("rolling_months", 12)),
        price_period_unit=raw.get("price_period_unit", "month"),
        ratchet=None
        if ratchet is None
        else Ratchet(
            fraction=float(ratchet["fraction"]),
            lookback_months=int(ratchet["lookback_months"]),
        ),
        coarse_factor=float(raw.get("coarse_factor", 1.15)),
    )


def _contracted(raw: Mapping[str, Any], currency: str) -> ContractedPower:
    surcharge = raw.get("surcharge_per_kw")
    return ContractedPower(
        limits=tuple(
            PeriodLimit(when=_filter(limit.get("when")), limit_kw=float(limit["limit_kw"]))
            for limit in raw["limits"]
        ),
        on_exceed=raw["on_exceed"],
        tolerance_pct=float(raw.get("tolerance_pct", 0.0)),
        tolerance_s=int(raw.get("tolerance_s", 0)),
        surcharge_per_kw=None if surcharge is None else Money(Decimal(str(surcharge)), currency),
        unit=raw.get("unit", "kw"),
        power_factor=float(raw.get("power_factor", 1.0)),
    )


def _filter(raw: Mapping[str, Any] | None) -> TimeFilter | None:
    return None if raw is None else _require_filter(raw)


def _require_filter(raw: Mapping[str, Any]) -> TimeFilter:
    months = raw.get("months")
    weekdays = raw.get("weekdays")
    hours = raw.get("hours")
    return TimeFilter(
        months=None if months is None else tuple(int(month) for month in months),
        weekdays=None if weekdays is None else tuple(int(day) for day in weekdays),
        hours=None if hours is None else tuple((int(start), int(end)) for start, end in hours),
        holidays=HolidayMode(raw.get("holidays", "ignore")),
    )


# --------------------------------------------------------------------------- #
# The summary a household checks against its bill (D2 §6, INV-67)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SummaryBand:
    """One row of the capacity table: a kW band and what it costs.

    For a `StepTable` the price is the step's fee per period; for `Tiers` it is
    the marginal price per kW inside the band. `upper_kw = None` is the open top.
    """

    lower_kw: float
    upper_kw: float | None
    price: Money


@dataclass(frozen=True, slots=True)
class EnergyRate:
    """One period of the grid's energy charge, in major units per kWh.

    `hours = None` is the rate for every hour no other period covers.
    """

    price: Decimal
    hours: tuple[tuple[int, int], ...] | None


@dataclass(frozen=True, slots=True)
class TariffSummary:
    """What a preset bills, as data (D2 §6; UX review R1, HUB-12).

    Core says *what* the tariff is and D8 says it in the household's language
    (`flow/text.py`), so nothing here is a sentence and nothing of it is stored
    in `entry.data`. `operator` is the grid company's own name, which is data.
    """

    operator: str
    currency: str
    peak: bool
    window_min: int = 60
    per_day: str = "max"
    per_period: str = "max"
    n: int = 1
    distinct_days: bool = True
    period: str = "month"
    rolling_months: int = 12
    price_period_unit: str = "month"
    #: `steps`, `linear` or `tiers`; `None` without a capacity component.
    pricing: str | None = None
    bands: tuple[SummaryBand, ...] = ()
    price_per_kw: Money | None = None
    free_kw: float = 0.0
    min_kw: float = 0.0
    eligible: TimeFilter | None = None
    weights: tuple[WeightRule, ...] = ()
    contracted_kw: tuple[float, ...] = ()
    contracted_unit: str = "kw"
    trips: bool = False
    energy: tuple[EnergyRate, ...] = ()
    source_url: str | None = None
    verified: str | None = None
    assumed: bool = False


def summarize(spec: TariffSpec, at: date | None = None) -> TariffSummary:
    """Return what the flow shows before it saves, as data (D2 §6, INV-67).

    The household should be able to recognise its own bill in the table D8
    renders from this; if it cannot, the preset is wrong and no amount of
    correct arithmetic will help.
    """
    version = spec.version_at(at) if at is not None else spec.versions[-1]
    contracted = version.contracted
    common: dict[str, Any] = {
        "operator": spec.operator or spec.name,
        "currency": spec.currency,
        "contracted_kw": ()
        if contracted is None
        else tuple(limit.limit_kw for limit in contracted.limits),
        "contracted_unit": "kw" if contracted is None else contracted.unit,
        "trips": contracted is not None and contracted.on_exceed == "trip",
        "energy": _energy_rates(version.energy_components),
        "source_url": version.source_url or spec.source_url,
        "verified": version.verified or spec.verified,
        "assumed": bool(version.assumed),
    }
    peak = version.peak
    if peak is None:
        return TariffSummary(peak=False, **common)
    pricing = peak.pricing
    bands: tuple[SummaryBand, ...] = ()
    price_per_kw: Money | None = None
    free_kw = min_kw = 0.0
    if isinstance(pricing, StepTable):
        kind = "steps"
        bands = tuple(
            SummaryBand(
                lower_kw=0.0 if index == 0 else pricing.upper_kw(index - 1),
                upper_kw=step.upper_kw,
                price=step.fee_per_period,
            )
            for index, step in enumerate(pricing.steps)
        )
    elif isinstance(pricing, Linear):
        kind = "linear"
        price_per_kw = pricing.price_per_kw
        free_kw, min_kw = pricing.free_kw, pricing.min_kw
    else:
        kind = "tiers"
        lower = 0.0
        rows: list[SummaryBand] = []
        for upper, price in pricing.bands:
            rows.append(SummaryBand(lower_kw=lower, upper_kw=upper, price=price))
            lower = lower if upper is None else upper
        bands = tuple(rows)
    return TariffSummary(
        peak=True,
        window_min=peak.window_min,
        per_day=peak.per_day,
        per_period=peak.per_period,
        n=peak.n,
        distinct_days=peak.distinct_days,
        period=peak.period,
        rolling_months=peak.rolling_months,
        price_period_unit=peak.price_period_unit,
        pricing=kind,
        bands=bands,
        price_per_kw=price_per_kw,
        free_kw=free_kw,
        min_kw=min_kw,
        eligible=peak.eligible,
        weights=peak.weights,
        **common,
    )


def _energy_rates(components: Mapping[str, Any]) -> tuple[EnergyRate, ...]:
    """Return the grid energy charge a preset carries for D1 (`tou_schedule`), if any."""
    periods = (components.get("tou_schedule") or {}).get("periods") or ()
    return tuple(
        EnergyRate(
            price=Decimal(str(period["price"])),
            hours=None
            if period.get("hours") is None
            else tuple((int(start), int(end)) for start, end in period["hours"]),
        )
        for period in periods
    )
