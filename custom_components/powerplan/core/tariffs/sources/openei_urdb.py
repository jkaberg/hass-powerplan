"""The US's rates from NREL's Utility Rate Database (D13 §5.5, §5.11; T1a).

[URDB](https://openei.org/services/doc/rest/util_rates/) describes a utility's
approved rate as periods and two 12 × 24 month-by-hour matrices, one for
weekdays and one for weekends, naming the period in force: `energyratestructure`
with `energyweekdayschedule`/`energyweekendschedule`, and the same for time-of-use
demand (`demandratestructure`, `demand…schedule`) and for all-hours demand
(`flatdemandstructure`, `flatdemandmonths`). Each period's price is its first
tier's `rate` + `adj`; a block rate on demand is refused (rule 9).

The rate's prices are bundled - supply and delivery in one - and the US has no
national VAT, so the copy is the grid party whole (D-0574). A demand charge that
changes with the season becomes one version per season over the twelve months
from the fetch; several demand charges in one month are several peak charges
(G4). URDB gives no demand window on most residential rates (F7): it is asked,
60 minutes pre-selected. Holidays are never marked: none is assumed. Pure.

`energy_periods` and `matrices` are the importer and its inverse, so a round trip
either reproduces the document or names the cell where it did not (D1 §9 7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from ...model import Money
from ..household import EXCL, EnergyPeriod, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import Linear, NoPeak, PeakTariff, TariffVersion, TimeFilter
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import tzinfo

    from ..model import HolidayCalendar

__all__ = [
    "API",
    "DEMO_KEY",
    "KEY",
    "UrdbError",
    "energy_periods",
    "matrices",
    "operators",
    "parse",
    "rate_url",
    "rates_url",
]

KEY: Final = "openei_urdb"
API: Final = "https://api.openei.org/utility_rates"
#: api.data.gov's shared key: 50 requests a day per address - a flow and the renewals.
DEMO_KEY: Final = "DEMO_KEY"
ATTRIBUTION: Final = "OpenEI Utility Rate Database (NREL)"
WEEKDAYS: Final = (0, 1, 2, 3, 4)
WEEKEND: Final = (5, 6)
_MONTHS: Final = 12
_HOURS: Final = 24
_STRUCTURE: Final = "energyratestructure"
_SCHEDULES: Final = (("energyweekdayschedule", WEEKDAYS), ("energyweekendschedule", WEEKEND))
_DEMAND: Final = (("demandweekdayschedule", WEEKDAYS), ("demandweekendschedule", WEEKEND))
_FEE_PER: Final[dict[str, Literal["day", "month", "year"]]] = {
    "$/day": "day",
    "$/month": "month",
    "$/year": "year",
}


class UrdbError(QualityError):
    """A URDB document the importer cannot read, named by the field and cell."""


def rates_url(postcode: str) -> str:
    """Return the approved residential rates of the utilities serving a ZIP code."""
    return (
        f"{API}?version=latest&format=json&api_key={DEMO_KEY}&detail=minimal"
        f"&approved=true&sector=Residential&address={postcode}&limit=500"
    )


def rate_url(label: str) -> str:
    """Return one rate in full, by its URDB label."""
    return f"{API}?version=latest&format=json&api_key={DEMO_KEY}&detail=full&getpage={label}"


def _items(document: bytes) -> list[Mapping[str, Any]]:
    try:
        answer = json.loads(document)
    except ValueError as err:
        msg = f"{KEY}: not JSON: {err}"
        raise UrdbError(msg) from err
    if "error" in answer:
        msg = f"{KEY}: {answer['error']}"
        raise UrdbError(msg)
    return list(answer.get("items") or ())


def operators(document: bytes, today: date) -> list[Operator]:
    """Return each utility serving the ZIP code, its rates in force as products."""
    found: dict[str, tuple[str, list[Product]]] = {}
    for item in _items(document):
        end = item.get("enddate")
        if end and datetime.fromtimestamp(int(end), UTC).date() < today:
            continue
        key = str(item["eiaid"])
        entry = found.setdefault(key, (str(item["utility"]), []))
        entry[1].append(Product(str(item["label"]), str(item["name"])))
    return sorted(
        (Operator(key, name, products=tuple(products)) for key, (name, products) in found.items()),
        key=lambda operator: operator.name,
    )


# --------------------------------------------------------------------------- #
# Energy: the 12 × 24 importer and its inverse (D1 §9 7)
# --------------------------------------------------------------------------- #


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _prices(rate: Mapping[str, Any], key: str) -> tuple[Decimal, ...]:
    structure = rate.get(key)
    if not isinstance(structure, list) or not structure:
        msg = f"the rate has no {key}"
        raise UrdbError(msg)
    return tuple(_decimal(p[0].get("rate", 0)) + _decimal(p[0].get("adj", 0)) for p in structure)


def _matrix(rate: Mapping[str, Any], key: str, periods: int) -> Sequence[Sequence[int]]:
    matrix = rate.get(key)
    if not isinstance(matrix, list) or len(matrix) != _MONTHS:
        msg = f"{key} must have {_MONTHS} rows of {_HOURS} hours"
        raise UrdbError(msg)
    for month, row in enumerate(matrix, start=1):
        if len(row) != _HOURS:
            msg = f"{key} month {month} has {len(row)} hours, not {_HOURS}"
            raise UrdbError(msg)
        for hour, period in enumerate(row):
            if not 0 <= period < periods:
                msg = f"{key} month {month} hour {hour} names period {period}, and there are {periods}"
                raise UrdbError(msg)
    return matrix


def _ranges(hours: Sequence[int]) -> tuple[tuple[int, int], ...]:
    """Return hours as minute ranges, merged; never wrapped past midnight (the day's filter)."""
    out: list[list[int]] = []
    for hour in hours:
        if out and out[-1][1] == hour * 60:
            out[-1][1] = (hour + 1) * 60
        else:
            out.append([hour * 60, (hour + 1) * 60])
    return tuple((start, end) for start, end in out)


def energy_periods(rate: Mapping[str, Any]) -> tuple[EnergyPeriod, ...]:
    """Return the energy schedules as periods: one per period, day kind and month pattern."""
    prices = _prices(rate, _STRUCTURE)
    periods: list[EnergyPeriod] = []
    for index, price in enumerate(prices):
        for key, weekdays in _SCHEDULES:
            matrix = _matrix(rate, key, len(prices))
            months_by_hours: dict[tuple[int, ...], list[int]] = {}
            for month, row in enumerate(matrix, start=1):
                hours = tuple(hour for hour, period in enumerate(row) if period == index)
                if hours:
                    months_by_hours.setdefault(hours, []).append(month)
            periods.extend(
                EnergyPeriod(
                    when=TimeFilter(months=tuple(months), weekdays=weekdays, hours=_ranges(hours)),
                    price=price,
                )
                for hours, months in months_by_hours.items()
            )
    return tuple(periods)


def matrices(
    version: EnergyVersion, year: int, tz: tzinfo, calendar: HolidayCalendar
) -> dict[str, Any]:
    """Return the URDB document an energy version prices like (D1 §9 7).

    Evaluated on the first ordinary day of each kind in each month; periods are
    numbered in the order their prices first appear, weekdays first.
    """
    index_of: dict[Decimal, int] = {}
    out: dict[str, Any] = {}
    for key, weekdays in _SCHEDULES:
        rows: list[list[int]] = []
        for month in range(1, _MONTHS + 1):
            day = date(year, month, 1)
            while day.weekday() not in weekdays or calendar.is_holiday(day):
                day += timedelta(days=1)
            row = []
            for hour in range(_HOURS):
                when = datetime.combine(day, time(hour=hour), tzinfo=tz)
                price = next(
                    (
                        p.price
                        for p in version.periods
                        if p.when is None or p.when.matches(when, tz, calendar)
                    ),
                    version.fallback,
                )
                row.append(index_of.setdefault(price, len(index_of)))
            rows.append(row)
        out[key] = rows
    out[_STRUCTURE] = [[{"rate": price}] for price in index_of]
    return out


# --------------------------------------------------------------------------- #
# Demand, versions and the copy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Charge:
    """One priced demand charge in one month: its price and its hours by day kind."""

    price: Decimal
    hours: tuple[tuple[tuple[int, ...], tuple[int, ...]], ...]  # (weekdays, hours)


def _demand_prices(rate: Mapping[str, Any], key: str) -> tuple[Decimal, ...]:
    structure = rate.get(key)
    if not structure:
        return ()
    for position, period in enumerate(structure):
        if len(period) > 1:
            msg = f"{KEY}: {key} period {position} is a block demand rate (rule 9)"
            raise UrdbError(msg)
    return _prices(rate, key)


def _month_charges(rate: Mapping[str, Any], month: int) -> tuple[_Charge, ...]:
    """Return the month's priced demand charges: all-hours first, then by time of use."""
    charges: list[_Charge] = []
    flat = _demand_prices(rate, "flatdemandstructure")
    flat_months = rate.get("flatdemandmonths")
    if flat and isinstance(flat_months, list) and len(flat_months) == _MONTHS:
        price = flat[flat_months[month - 1]]
        if price > 0:
            charges.append(_Charge(price=price, hours=((WEEKDAYS + WEEKEND, ()),)))
    tou = _demand_prices(rate, "demandratestructure")
    if tou:
        for index, price in enumerate(tou):
            if price <= 0:
                continue
            kinds = []
            for key, weekdays in _DEMAND:
                row = _matrix(rate, key, len(tou))[month - 1]
                hours = tuple(hour for hour, period in enumerate(row) if period == index)
                if hours:
                    kinds.append((weekdays, hours))
            if kinds:
                charges.append(_Charge(price=price, hours=tuple(kinds)))
    return tuple(charges)


def _peak(charge: _Charge, window: Literal[15, 30, 60], currency: str) -> PeakTariff:
    kinds = charge.hours
    if len({hours for _, hours in kinds}) > 1:
        msg = f"{KEY}: a demand charge with different hours on weekdays and weekends"
        raise UrdbError(msg)
    weekdays = tuple(sorted(day for days, _ in kinds for day in days))
    hours = kinds[0][1]
    everyday = weekdays == WEEKDAYS + WEEKEND
    eligible = (
        None
        if everyday and (not hours or len(hours) == _HOURS)
        else TimeFilter(
            weekdays=None if everyday else weekdays,
            hours=_ranges(hours) if hours and len(hours) < _HOURS else None,
        )
    )
    return PeakTariff(
        window_min=window,
        eligible=eligible,
        weights=(),
        per_day="max",
        per_period="max",
        period="month",
        pricing=Linear(price_per_kw=Money(charge.price, currency)),
    )


def _window(rate: Mapping[str, Any], answers: Mapping[str, Any]) -> Literal[15, 30, 60]:
    given = answers.get("window_min", rate.get("demandwindow") or 60)
    minutes = int(given)
    if minutes not in {15, 30, 60}:
        msg = f"{KEY}: a {minutes}-minute demand window the model cannot say"
        raise UrdbError(msg)
    return minutes  # type: ignore[return-value]


def _months_ahead(start: date) -> list[date]:
    months = []
    year, month = start.year, start.month
    for _ in range(_MONTHS):
        months.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == _MONTHS else (year, month + 1)
    return months


def parse(
    document: bytes,
    operator: str,
    *,
    fetched: date,
    answers: Mapping[str, Any],
) -> Fetched:
    """Return one rate as the copy: a version per season over the twelve months from the fetch."""
    items = _items(document)
    if len(items) != 1:
        msg = f"{KEY}: expected one rate, got {len(items)}"
        raise UrdbError(msg)
    rate = items[0]
    currency = "USD"
    window = _window(rate, answers)
    questions: list[Question] = []
    if "demandwindow" not in rate and "window_min" not in answers:
        questions.append(
            Question(
                "window_min",
                "60",
                "The rate database does not say over how many minutes demand is measured.",
                ("15", "30", "60"),
            )
        )
    # an approved rate with no start date is in force: from the fetch's month
    month = date(fetched.year, fetched.month, 1)
    start = (
        datetime.fromtimestamp(int(rate["startdate"]), UTC).date() if "startdate" in rate else month
    )
    first = max(start, month)
    periods = energy_periods(rate) if rate.get(_STRUCTURE) else ()
    capacity: list[TariffVersion] = []
    energy: list[EnergyVersion] = []
    last: tuple[_Charge, ...] | None = None
    for month_start in _months_ahead(first):
        charges = _month_charges(rate, month_start.month)
        if charges == last:
            continue
        last = charges
        since = max(month_start, start)
        rules = tuple(_peak(charge, window, currency) for charge in charges) or (NoPeak(),)
        capacity.append(
            TariffVersion(
                valid_from=since,
                version_id=f"{slug('us', operator, str(rate['label']), KEY)}@{since.isoformat()}",
                rules=rules,
                verified=fetched.isoformat(),
                source_url=str(rate.get("source") or rate.get("uri") or ""),
            )
        )
        energy.append(EnergyVersion(valid_from=since, periods=periods))
    fees: tuple[FeeVersion, ...] = ()
    fixed = rate.get("fixedchargefirstmeter")
    if fixed:
        per = _FEE_PER.get(str(rate.get("fixedchargeunits") or "$/month"))
        if per is None:
            msg = f"{KEY}: a fixed charge in {rate.get('fixedchargeunits')}"
            raise UrdbError(msg)
        fees = (FeeVersion(valid_from=capacity[0].valid_from, amount=_decimal(fixed), per=per),)
    end = rate.get("enddate")
    grid = GridTariff(
        operator=str(rate["utility"]),
        product=str(rate["name"]),
        provenance=Provenance(
            source=KEY,
            url=str(rate.get("uri") or rate_url(str(rate["label"]))),
            fetched=fetched,
            attribution=ATTRIBUTION,
            tier="T1a",
        ),
        currency=currency,
        basis=EXCL,
        capacity=tuple(capacity),
        energy=tuple(energy),
        fixed_fee=fees,
        capacity_id=slug("us", operator, str(rate["label"]), KEY),
        valid_to=None
        if not end
        else datetime.fromtimestamp(int(end), UTC).date() - timedelta(days=1),
        operator_key=operator,
        product_key=str(rate["label"]),
    )
    return Fetched(grid=grid, questions=tuple(questions))
