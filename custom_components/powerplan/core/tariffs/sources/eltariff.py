"""Sweden's grid tariffs from the Eltariff standard (D13 §5.5, §5.11; T1a).

[Eltariff](https://github.com/RI-SE/Eltariff-API) is the industry's open API:
a catalogue at `eltariff.se` names each company's endpoint, and each endpoint's
`GET /tariffs` publishes every tariff with its prices **excl. and incl. VAT**
and its components - fixed (`fixedPrice`), per kWh (`energyPrice`) and per kW
(`powerPrice`) - each with its own validity and hours. The copy reads the prices
excl. VAT; the energy tax a company lists (`reference: "tax"`) is the state's
and is left to the SE module (INV-72). Pure: the documents in, a copy out.

A version starts wherever any component's validity starts, so a winter power
price is a version (Göteborg: November to March). Two power prices at once -
Tekniska verken's day and night peak - wait for D13 §18 G4 and are
refused; a reactive-power charge is a business tariff's, and never offered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Final, Literal

from ...model import Money
from ..household import (
    EXCL,
    EnergyPeriod,
    EnergyVersion,
    FeeVersion,
    GridTariff,
    Provenance,
)
from ..model import HolidayMode, Linear, NoPeak, PeakTariff, TariffVersion, TimeFilter
from .base import Fetched, Operator, Product, QualityError, slug

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["CATALOGUE", "KEY", "household", "operators", "parse"]

KEY: Final = "eltariff"
CATALOGUE: Final = "https://eltariff.se/tariffcatalogue/all"
ATTRIBUTION: Final = "Eltariff (RISE and the Swedish grid companies)"
#: A validity this far off is "until further notice" (E.ON writes 2199-12-31).
_OPEN_ENDED: Final = date(2100, 1, 1)
#: A household's connection: a fuse of at most 63 A, or a flat (D-0567).
_HOUSEHOLD_FUSE_A: Final = 63
_FUSE: Final = re.compile(r"(\d+)\s*A\b")
_FLAT: Final = re.compile(r"lägenhet|\blgh\b|apartment", re.IGNORECASE)
_HIGH_VOLTAGE: Final = re.compile(r"\d+(,\d+)?\s*kV\b|högspänning", re.IGNORECASE)
_ABOVE: Final = re.compile(r"\b(över|over|above)\b", re.IGNORECASE)
_PEAK_FUNCTION: Final = re.compile(r"^peak\((\w+)\)$")
_DURATION_MIN: Final[dict[str, Literal[15, 30, 60]]] = {"PT15M": 15, "PT30M": 30, "PT1H": 60}
_FEE_PER: Final = {"P1M": "month", "P1Y": "year", "P1D": "day"}
_MINUTES_PER_DAY: Final = 24 * 60


@dataclass(frozen=True, slots=True)
class _Pattern:
    """A calendar pattern: ISO weekdays, or the country's public holidays."""

    days: frozenset[int]
    holidays: bool


# --------------------------------------------------------------------------- #
# Operators and products
# --------------------------------------------------------------------------- #


def operators(
    catalogue: Sequence[Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
    zones: tuple[str, ...] = (),
) -> list[Operator]:
    """Return each catalogue company once, keyed by organisation number, with its products.

    `documents` are the companies' `GET /tariffs` answers by `apiUrl`; a company
    whose endpoint did not answer is left out. `zones` are the country's tax
    zones where the source cannot place a company in one (SE: D-0568).
    """
    found: dict[str, Operator] = {}
    for entry in catalogue:
        org = str(entry["companyOrgNo"])
        document = documents.get(str(entry["apiUrl"]))
        if org in found or document is None:
            continue
        offered = {
            str(tariff["product"]): str(tariff["name"]) for tariff in household(document, org)
        }
        if not offered:
            continue
        found[org] = Operator(
            key=org,
            name=str(entry["companyName"]),
            products=tuple(Product(code, name) for code, name in sorted(offered.items())),
            zones=zones,
        )
    return sorted(found.values(), key=lambda operator: operator.name)


def household(document: Mapping[str, Any], org: str) -> list[Mapping[str, Any]]:
    """Return a company's consumption tariffs a household can have (D-0567).

    A fuse of at most 63 A or a flat, never high voltage, never a reactive charge.
    """
    offered: list[Mapping[str, Any]] = []
    for tariff in document.get("tariffs") or ():
        if str(tariff.get("companyOrgNo")) != org or tariff.get("direction") != "consumption":
            continue
        name = str(tariff.get("name") or "")
        fuse = _FUSE.search(name)
        small = bool(_FLAT.search(name)) or (
            fuse is not None and int(fuse.group(1)) <= _HOUSEHOLD_FUSE_A
        )
        if not small or _ABOVE.search(name) or _HIGH_VOLTAGE.search(name) or _reactive(tariff):
            continue
        offered.append(tariff)
    return offered


def _reactive(tariff: Mapping[str, Any]) -> bool:
    return any(
        component.get("unit") == "kVAr"
        or "reactive"
        in str((component.get("peakIdentificationSettings") or {}).get("peakFunction"))
        for component in _components(tariff, "powerPrice")
    )


# --------------------------------------------------------------------------- #
# One product
# --------------------------------------------------------------------------- #


def parse(
    document: Mapping[str, Any],
    org: str,
    product: str,
    *,
    fetched: date,
    url: str,
) -> Fetched:
    """Return one company's product as the copy: every version its tariffs publish."""
    tariffs = [t for t in household(document, org) if str(t["product"]) == product]
    if not tariffs:
        msg = f"{KEY}: {org} publishes no household tariff {product!r}"
        raise QualityError(msg)
    patterns = _patterns(document)
    capacity: list[TariffVersion] = []
    energy: list[EnergyVersion] = []
    fees: list[FeeVersion] = []
    last: date | None = None
    for tariff in sorted(tariffs, key=lambda t: str(t["validPeriod"]["fromIncluding"])):
        start, end = _period(tariff["validPeriod"])
        for since, until in _segments(tariff, start, end):
            active = _active(tariff, since)
            if not [c for c in active["energyPrice"] if c.get("reference") != "tax"]:
                # The energy price is not yet published this far: the copy ends here,
                # and the renewal fetches the rest before it is needed (§10).
                last = since
                break
            rules = (_power(active["powerPrice"], patterns, org),)
            charge = _energy(active["energyPrice"], since, patterns, org)
            fee = _summed(_fees(active["fixedPrice"], since))
            last = until
            if capacity and _same(capacity[-1].rules, energy[-1], fees, rules, charge, fee):
                continue  # nothing the household pays changed at this cut
            capacity.append(
                TariffVersion(
                    valid_from=since,
                    version_id=f"{slug('se', org, product, KEY)}@{since.isoformat()}",
                    rules=rules,
                    verified=fetched.isoformat(),
                    source_url=url,
                )
            )
            energy.append(charge)
            fees.extend(fee)
    grid = GridTariff(
        operator=str(tariffs[0]["companyName"]),
        product=str(tariffs[-1]["name"]),
        provenance=Provenance(
            source=KEY, url=url, fetched=fetched, attribution=ATTRIBUTION, tier="T1a"
        ),
        currency=_currency(tariffs),
        basis=EXCL,
        capacity=tuple(capacity),
        energy=tuple(energy),
        fixed_fee=tuple(fees),
        capacity_id=slug("se", org, product, KEY),
        valid_to=None if last is None or last >= _OPEN_ENDED else last - timedelta(days=1),
        operator_key=org,
        product_key=product,
    )
    return Fetched(grid=grid)


def _same(  # noqa: PLR0917 - the previous version's three parts and this one's
    rules: tuple[object, ...],
    charge: EnergyVersion,
    fees: Sequence[FeeVersion],
    new_rules: tuple[object, ...],
    new_charge: EnergyVersion,
    new_fees: Sequence[FeeVersion],
) -> bool:
    last_fee = (fees[-1].amount, fees[-1].per) if fees else None
    new_fee = (new_fees[0].amount, new_fees[0].per) if new_fees else None
    return (
        rules == new_rules
        and (charge.periods, charge.fallback, charge.spot_share)
        == (new_charge.periods, new_charge.fallback, new_charge.spot_share)
        and last_fee == new_fee
    )


def _currency(tariffs: Sequence[Mapping[str, Any]]) -> str:
    for tariff in tariffs:
        for section in ("fixedPrice", "energyPrice", "powerPrice"):
            for component in _components(tariff, section):
                currency = (component.get("price") or {}).get("currency")
                if currency:
                    return str(currency)
    return "SEK"


def _components(tariff: Mapping[str, Any], section: str) -> list[Mapping[str, Any]]:
    return list((tariff.get(section) or {}).get("components") or ())


def _period(period: Mapping[str, Any]) -> tuple[date, date]:
    return date.fromisoformat(str(period["fromIncluding"])), date.fromisoformat(
        str(period["toExcluding"])
    )


def _segments(tariff: Mapping[str, Any], start: date, end: date) -> list[tuple[date, date]]:
    """Split a tariff's validity wherever one of its components starts or ends."""
    cuts = {start, end}
    for section in ("fixedPrice", "energyPrice", "powerPrice"):
        for component in _components(tariff, section):
            if component.get("reference") == "tax":
                continue  # the state's, not the copy's: its dates are the SE module's
            since, until = _period(component["validPeriod"])
            cuts |= {max(start, min(since, end)), max(start, min(until, end))}
    ordered = sorted(cuts)
    return [(a, b) for a, b in pairwise(ordered) if a < b]


def _active(tariff: Mapping[str, Any], day: date) -> dict[str, list[Mapping[str, Any]]]:
    """Return each section's components valid on `day`."""
    found: dict[str, list[Mapping[str, Any]]] = {}
    for section in ("fixedPrice", "energyPrice", "powerPrice"):
        found[section] = []
        for component in _components(tariff, section):
            since, until = _period(component["validPeriod"])
            if since <= day < until:
                found[section].append(component)
    return found


def _price(component: Mapping[str, Any]) -> Decimal:
    return Decimal(str((component.get("price") or {}).get("priceExVat") or 0))


# --------------------------------------------------------------------------- #
# Power, energy and fees
# --------------------------------------------------------------------------- #


def _power(
    components: Sequence[Mapping[str, Any]], patterns: Mapping[str, _Pattern], org: str
) -> PeakTariff | NoPeak:
    """Return the version's power price; a zero-priced component bills nothing."""
    priced = [component for component in components if _price(component) > 0]
    if not priced:
        return NoPeak()
    if len(priced) > 1:
        msg = f"{KEY}: {org} bills {len(priced)} power prices at once (D13 §18 G4, TS.5)"
        raise QualityError(msg)
    component = priced[0]
    settings = component.get("peakIdentificationSettings") or {}
    function = _PEAK_FUNCTION.match(str(settings.get("peakFunction") or "peak(main)"))
    window = _DURATION_MIN.get(str(settings.get("peakDuration") or "PT1H"))
    period = str(settings.get("peakIdentificationPeriod") or "P1M")
    n = int(settings.get("numberOfPeaksForAverageCalculation") or 1)
    if function is None or window is None or period not in {"P1D", "P1M"}:
        msg = f"{KEY}: {org}'s power price is measured in a way the model cannot say: {settings}"
        raise QualityError(msg)
    eligible = _eligible(component, patterns, org)
    per_period: Literal["max", "mean_top_n"] = "mean_top_n" if period == "P1D" and n > 1 else "max"
    return PeakTariff(
        window_min=window,
        eligible=eligible,
        weights=(),
        per_day="max",
        per_period=per_period,
        n=n if per_period == "mean_top_n" else 1,
        distinct_days=per_period == "mean_top_n",
        period="month",
        pricing=Linear(price_per_kw=Money(_price(component), str(component["price"]["currency"]))),
    )


def _eligible(
    component: Mapping[str, Any], patterns: Mapping[str, _Pattern], org: str
) -> TimeFilter | None:
    """One filter for a power price's hours; hours that need two filters are refused."""
    filters = _filters(component, patterns, org)
    if not filters:
        return None
    if len({(f.weekdays, f.holidays) for f in filters}) > 1:
        msg = f"{KEY}: {org}'s power hours differ by day type (D13 §18 G4, TS.5)"
        raise QualityError(msg)
    hours = tuple(h for f in filters for h in (f.hours or ()))
    merged = TimeFilter(
        weekdays=filters[0].weekdays,
        hours=None if any(f.hours is None for f in filters) else hours,
        holidays=filters[0].holidays,
    )
    return None if merged == TimeFilter() else merged


def _energy(
    components: Sequence[Mapping[str, Any]],
    since: date,
    patterns: Mapping[str, _Pattern],
    org: str,
) -> EnergyVersion:
    """Return the grid's energy charge: timed components as periods over the untimed sum."""
    base = Decimal(0)
    share = Decimal(0)
    timed: list[tuple[TimeFilter, Decimal, str]] = []
    for component in components:
        if component.get("reference") == "tax":
            continue  # the state's energy tax: the SE module's (INV-72)
        if component.get("type") == "spot":
            share += Decimal(str((component.get("spotPriceSettings") or {}).get("multiplier") or 0))
            continue
        filters = _filters(component, patterns, org)
        if not filters:
            base += _price(component)
            continue
        timed.extend((f, _price(component), str(component.get("reference") or "")) for f in filters)
    periods = tuple(
        EnergyPeriod(when=when, price=base + price, name=name) for when, price, name in timed
    )
    return EnergyVersion(valid_from=since, periods=periods, fallback=base, spot_share=share)


def _fees(components: Sequence[Mapping[str, Any]], since: date) -> list[FeeVersion]:
    return [
        FeeVersion(
            valid_from=since,
            amount=_price(component),
            per=_FEE_PER.get(str(component.get("pricedPeriod") or "P1M"), "month"),  # type: ignore[arg-type]
        )
        for component in components
        if _price(component)
    ]


def _summed(fees: Sequence[FeeVersion]) -> tuple[FeeVersion, ...]:
    """One fee per start, in its components' common unit - yearly where they differ."""
    by_start: dict[date, list[FeeVersion]] = {}
    for fee in fees:
        by_start.setdefault(fee.valid_from, []).append(fee)
    per_year = {"year": Decimal(1), "month": Decimal(12), "day": Decimal(365)}
    summed: list[FeeVersion] = []
    for start, group in sorted(by_start.items()):
        units = {fee.per for fee in group}
        if len(units) == 1:
            summed.append(
                FeeVersion(start, sum((f.amount for f in group), Decimal(0)), group[0].per)
            )
        else:
            yearly = sum((f.amount * per_year[f.per] for f in group), Decimal(0))
            summed.append(FeeVersion(start, yearly, "year"))
    return tuple(summed)


# --------------------------------------------------------------------------- #
# Hours and days
# --------------------------------------------------------------------------- #


def _patterns(document: Mapping[str, Any]) -> dict[str, _Pattern]:
    """Return the document's calendar patterns; one with dates is refused where used."""
    found: dict[str, _Pattern] = {}
    for pattern in document.get("calendarPatterns") or ():
        reference = pattern.get("reference")
        if reference is None or pattern.get("dates"):
            continue
        days = frozenset(int(day) for day in pattern.get("days") or ())
        found[str(reference)] = _Pattern(days=days, holidays=not days and reference == "holidays")
    return found


def _filters(
    component: Mapping[str, Any], patterns: Mapping[str, _Pattern], org: str
) -> list[TimeFilter]:
    """Return a component's active periods as filters; all day, every day is none."""
    filters: list[TimeFilter] = []
    for recurring in component.get("recurringPeriods") or ():
        if recurring.get("frequency") not in {None, "P1D"}:
            msg = f"{KEY}: {org} repeats a period by {recurring.get('frequency')}"
            raise QualityError(msg)
        filters.extend(
            _filter(active, patterns, org) for active in recurring.get("activePeriods") or ()
        )
    if all(f == TimeFilter() for f in filters):
        return []
    return filters


def _filter(active: Mapping[str, Any], patterns: Mapping[str, _Pattern], org: str) -> TimeFilter:
    start = _minutes(str(active["fromIncluding"]))
    end = _minutes(str(active["toExcluding"]))
    hours = None if start == end else ((start, end),)
    references = active.get("calendarPatternReferences") or {}
    include = [patterns.get(str(r)) for r in references.get("include") or ()]
    exclude = [patterns.get(str(r)) for r in references.get("exclude") or ()]
    if None in include or None in exclude:
        msg = f"{KEY}: {org} names a calendar pattern it does not define"
        raise QualityError(msg)
    days = frozenset().union(*(p.days for p in include if p is not None))
    with_holidays = any(p.holidays for p in include if p is not None)
    without_holidays = any(p.holidays for p in exclude if p is not None)
    if any(p.days for p in exclude if p is not None):
        msg = f"{KEY}: {org} excludes weekdays from a pattern"
        raise QualityError(msg)
    weekdays = (
        None
        if days in (frozenset(), frozenset(range(1, 8)))
        else tuple(sorted(day - 1 for day in days))
    )
    if with_holidays and weekdays is not None and 6 not in weekdays:  # noqa: PLR2004 - Sunday
        msg = f"{KEY}: {org} includes holidays beside days that are not Sunday's"
        raise QualityError(msg)
    holidays = (
        HolidayMode.EXCLUDE
        if without_holidays
        else HolidayMode.AS_SUNDAY
        if with_holidays and weekdays is not None
        else HolidayMode.IGNORE
    )
    return TimeFilter(weekdays=weekdays, hours=hours, holidays=holidays)


def _minutes(clock: str) -> int:
    hours, minutes, *_ = (int(part) for part in clock.split(":"))
    return (hours * 60 + minutes) % _MINUTES_PER_DAY
