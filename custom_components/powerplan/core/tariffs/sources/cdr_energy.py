"""Australia's plans from the Consumer Data Right (D13 §5.5, §5.11; T1a).

The CDR register lists every energy retailer's brand and its public base; each
brand's `GET …/cds-au/v1/energy/plans` lists its plans with their postcodes, and
`…/plans/{planId}` gives one in full: per tariff period (a date range each year)
the energy rates - one rate or time-of-use windows by day - the daily supply
charge and the demand charges, all excl. GST (the CDR's own rule), in dollars.
A retail plan bundles network and energy, so the copy is the grid party whole
and GST is the AU module's (D-0574).

A demand charge gives its window (`startTime`–`endTime`, days), its amount per
`chargePeriod` (a day: G9) and its unit (kW, or kVA at a power factor: G10). Its
measurement period is not trusted (F7: a `DAY` beside "the maximum half-hourly kW
… over the 12 months prior"), so it is always asked, the description's reading
pre-selected; where the name says cents the price per kW is asked in dollars, the
amount ÷ 100 pre-selected. Pure.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from ...model import Money
from ..household import EXCL, EnergyPeriod, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import Linear, NoPeak, PeakTariff, Ratchet, TariffVersion, TimeFilter
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["KEY", "REGISTER", "brands", "parse", "plan_url", "plans_url", "products"]

KEY: Final = "cdr_energy"
REGISTER: Final = "https://api.cdr.gov.au/cdr-register/v1/energy/data-holders/brands/summary"
ATTRIBUTION: Final = "Consumer Data Right (the brand's product reference data)"
_DAYS: Final = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
#: The NEM's metering interval: a demand reading is a half-hour's.
_WINDOW: Final = 30
MEASUREMENTS: Final = ("day", "month", "year")
_MINUTES_PER_DAY: Final = 24 * 60
_CENTS_PER_DOLLAR: Final = 100


def _json(document: bytes) -> Any:
    try:
        return json.loads(document)
    except ValueError as err:
        msg = f"{KEY}: not JSON: {err}"
        raise QualityError(msg) from err


def plans_url(base: str) -> str:
    """Return a brand's current electricity market plans, one page of up to 1 000."""
    return (
        f"{base.rstrip('/')}/cds-au/v1/energy/plans"
        "?type=MARKET&fuelType=ELECTRICITY&effective=CURRENT&page-size=1000"
    )


def plan_url(base: str, plan: str) -> str:
    """Return one plan in full."""
    return f"{base.rstrip('/')}/cds-au/v1/energy/plans/{plan}"


def brands(register: bytes) -> tuple[list[Operator], dict[str, str]]:
    """Return the register's energy brands as operators, and each one's public base."""
    found: list[Operator] = []
    bases: dict[str, str] = {}
    for brand in _json(register).get("data") or ():
        key = str(brand.get("dataHolderBrandId") or brand["interimId"])
        bases[key] = str(brand["publicBaseUri"])
        found.append(Operator(key, str(brand["brandName"])))
    return sorted(found, key=lambda operator: operator.name), bases


def products(document: bytes, postcode: str | None) -> tuple[Product, ...]:
    """Return a brand's residential electricity plans, those for the postcode where given."""
    found: list[Product] = []
    for plan in (_json(document).get("data") or {}).get("plans") or ():
        if plan.get("customerType") != "RESIDENTIAL":
            continue
        geography = plan.get("geography") or {}
        if postcode and (
            postcode in (geography.get("excludedPostcodes") or ())
            or (
                geography.get("includedPostcodes")
                and postcode not in geography["includedPostcodes"]
            )
        ):
            continue
        found.append(Product(str(plan["planId"]), str(plan["displayName"])))
    return tuple(sorted(found, key=lambda product: product.name))


def _minutes(clock: str) -> int:
    hours, minutes = (int(part) for part in clock.split(":")[:2])
    return hours * 60 + minutes


def _window(entry: Mapping[str, Any]) -> tuple[int, int] | None:
    """`startTime`–`endTime`, inclusive of its last minute ("09:59"); all day is `None`."""
    start = _minutes(str(entry.get("startTime") or "00:00"))
    end = _minutes(str(entry.get("endTime") or "00:00"))
    end = end + 1 if end % 60 == 59 else end  # noqa: PLR2004 - "09:59" ends at 10:00
    if start == end % _MINUTES_PER_DAY:
        return None
    return start, end % _MINUTES_PER_DAY if end > _MINUTES_PER_DAY else end


def _filter(entry: Mapping[str, Any]) -> TimeFilter:
    days = tuple(sorted(_DAYS[str(day)] for day in entry.get("days") or _DAYS))
    window = _window(entry)
    return TimeFilter(
        weekdays=None if len(days) == len(_DAYS) else days,
        hours=None if window is None else (window,),
    )


def _energy(period: Mapping[str, Any], since: date) -> EnergyVersion:
    kind = period.get("rateBlockUType")
    if kind == "singleRate":
        rates = (period.get("singleRate") or {}).get("rates") or ()
        return EnergyVersion(valid_from=since, periods=(), fallback=_rate(rates))
    if kind == "timeOfUseRates":
        periods = tuple(
            EnergyPeriod(
                when=_filter(window),
                price=_rate(block.get("rates") or ()),
                name=str(block.get("type") or "").lower(),
            )
            for block in period.get("timeOfUseRates") or ()
            for window in block.get("timeOfUse") or ()
        )
        return EnergyVersion(valid_from=since, periods=periods)
    msg = f"{KEY}: a {kind} rate block the model cannot say"
    raise QualityError(msg)


def _rate(rates: Sequence[Mapping[str, Any]]) -> Decimal:
    """Return the first block's price: a usage block rate is the supplier's tier, not the grid's."""
    if not rates:
        msg = f"{KEY}: a rate with no price"
        raise QualityError(msg)
    return Decimal(str(rates[0]["unitPrice"]))


def _reading(description: str) -> str:
    text = description.lower()
    if "12 months" in text or "twelve months" in text or "year" in text:
        return "year"
    if "month" in text:
        return "month"
    return "day"


def _demand(
    charge: Mapping[str, Any], answers: Mapping[str, Any], questions: list[Question]
) -> PeakTariff:
    description = str(charge.get("description") or "")
    name = str(charge.get("displayName") or "")
    amount = Decimal(str(charge.get("amount") or 0))
    in_cents = "cent" in name.lower()
    questions.append(
        Question(
            "measurement",
            _reading(description),
            f"The plan's demand is measured by {charge.get('measurementPeriod')}, and says: "
            f"“{description}” – which is it?",
            MEASUREMENTS,
        )
    )
    if in_cents:
        questions.append(
            Question(
                "demand_price",
                float(amount / _CENTS_PER_DOLLAR),
                f"The plan names the charge “{name}” with the amount {amount}: the price per kW "
                "per day in dollars.",
            )
        )
    price = Decimal(
        str(answers.get("demand_price", amount / _CENTS_PER_DOLLAR if in_cents else amount))
    )
    unit: Literal["kw", "kva"] = "kva" if str(charge.get("measureUnit")).upper() == "KVA" else "kw"
    factor = 1.0
    if unit == "kva":
        questions.append(Question("power_factor", 0.9, "The charge is per kVA: your power factor."))
        factor = float(answers.get("power_factor", 0.9))
    measurement = str(answers.get("measurement", _reading(description)))
    if measurement not in MEASUREMENTS:
        msg = f"{KEY}: a measurement period {measurement!r}"
        raise QualityError(msg)
    charge_period = str(charge.get("chargePeriod") or "MONTH").upper()
    unit_of_price: Literal["month", "year", "day"] = (
        "day" if charge_period == "DAY" else "year" if charge_period == "YEAR" else "month"
    )
    eligible = _filter(charge)
    return PeakTariff(
        window_min=_WINDOW,
        eligible=None if eligible == TimeFilter() else eligible,
        weights=(),
        per_day="max",
        # "day": each day at its own maximum - the month's mean of daily maxima × days.
        per_period="mean_top_n" if measurement == "day" else "max",
        n=31 if measurement == "day" else 1,
        period="month",
        pricing=Linear(
            price_per_kw=Money(price, "AUD"), min_kw=float(charge.get("minDemand") or 0)
        ),
        price_period_unit=unit_of_price,
        # The maximum over the 12 months before the bill: this month's, or the year's.
        ratchet=Ratchet(fraction=1.0, lookback_months=12) if measurement == "year" else None,
        unit=unit,
        power_factor=factor,
    )


def _season_start(period: Mapping[str, Any], fetched: date) -> date:
    month, day = (int(part) for part in str(period.get("startDate") or "01-01").split("-"))
    start = date(fetched.year, month, day)
    return start if start <= fetched else date(fetched.year - 1, month, day)


def parse(
    document: bytes, brand: str, *, fetched: date, url: str, answers: Mapping[str, Any]
) -> Fetched:
    """Return one plan as the copy: a version per tariff period, from the season in force."""
    plan = (_json(document).get("data")) or {}
    contract = plan.get("electricityContract") or {}
    periods = sorted(contract.get("tariffPeriod") or (), key=lambda p: _season_start(p, fetched))
    if not periods:
        msg = f"{KEY}: the plan has no tariff period"
        raise QualityError(msg)
    questions: list[Question] = []
    capacity: list[TariffVersion] = []
    energy: list[EnergyVersion] = []
    fees: list[FeeVersion] = []
    for period in periods:
        since = _season_start(period, fetched)
        charges = period.get("demandCharges") or ()
        rules = tuple(_demand(charge, answers, questions) for charge in charges) or (NoPeak(),)
        capacity.append(
            TariffVersion(
                valid_from=since,
                version_id=f"{slug('au', str(plan.get('planId', '')), KEY)}@{since.isoformat()}",
                rules=rules,
                verified=fetched.isoformat(),
                source_url=url,
            )
        )
        energy.append(_energy(period, since))
        supply = period.get("dailySupplyCharge")
        if supply is not None:
            fees.append(FeeVersion(valid_from=since, amount=Decimal(str(supply)), per="day"))
    end = plan.get("effectiveTo")
    asked = {q.key: q for q in questions if q.key not in answers}
    grid = GridTariff(
        operator=str(plan.get("brandName") or brand),
        product=str(plan.get("displayName") or ""),
        provenance=Provenance(
            source=KEY, url=url, fetched=fetched, attribution=ATTRIBUTION, tier="T1a"
        ),
        currency="AUD",
        basis=EXCL,
        capacity=tuple(capacity),
        energy=tuple(energy),
        fixed_fee=tuple(fees),
        capacity_id=slug("au", str(plan.get("planId", "")), KEY),
        valid_to=None if not end else date.fromisoformat(str(end)[:10]) - timedelta(days=1),
        operator_key=brand,
        product_key=str(plan.get("planId") or ""),
    )
    return Fetched(grid=grid, questions=tuple(asked.values()))
