"""Norway's grid tariffs from fri-nettleie (D13 §5.4, §5.11; O16).

[kraftsystemet/fri-nettleie](https://github.com/kraftsystemet/fri-nettleie) keeps one
YAML file per grid company (CC BY 4.0), collected from the operator's own price
page, which each file names under `kilder`. Its numbers are **excluding VAT and
levies** - the fixed fee per step in NOK per year, the energy charge in øre per kWh
with `unntak` for hours, days and months - so the copy's basis is `EXCL` and the
state stage adds the zone's taxes (INV-71). Pure: the parsed documents in, a
`GridTariff` out.

Every method a household tariff uses maps through a closed table (§3, rule 9):
`TRE_DØGNMAX_MND`, `MND_MAX`, `FEM_VEKTET_ÅR` (G6) and `OV_TREFASE` (a fee by the
main fuse). `UKJENT` - the collector could not read the company's rule - is a
fact missing, not a fact wrong: it is asked, the usual rule pre-selected (rule 8).
A file older than twelve months, or with no version valid today, is shown with
its date for the household to confirm (§5.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ...model import Money
from ..household import (
    EXCL,
    EnergyPeriod,
    EnergyVersion,
    FeeVersion,
    GridTariff,
    Provenance,
)
from ..model import (
    HolidayMode,
    NoPeak,
    PeakTariff,
    Step,
    StepTable,
    TariffRule,
    TariffVersion,
    TimeFilter,
    WeightRule,
)
from .base import Fetched, Operator, Product, QualityError, Question

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "ANSWERS",
    "KEY",
    "METHODS",
    "REPOSITORY",
    "TARBALL",
    "operators",
    "parse",
    "products",
]

KEY: Final = "fri_nettleie"
REPOSITORY: Final = "https://github.com/kraftsystemet/fri-nettleie"
TARBALL: Final = "https://codeload.github.com/kraftsystemet/fri-nettleie/tar.gz/refs/heads/main"
ATTRIBUTION: Final = "Fri Nettleie (CC BY 4.0)"
HOUSEHOLD: Final = "husholdning"
CABIN: Final = "fritid"
#: §5.4's staleness guard: a file last checked longer ago than this is confirmed.
STALE_AFTER: Final = timedelta(days=365)

#: The methods fri-nettleie names (`tariff-eksempel.yml`) and what they mean here.
METHODS: Final = ("TRE_DØGNMAX_MND", "MND_MAX", "FEM_VEKTET_ÅR", "OV_TREFASE")
#: The same methods as the flow offers them: HA's option keys are `[a-z0-9_]`.
ANSWERS: Final = {method.lower().replace("ø", "o").replace("å", "a"): method for method in METHODS}
#: `UKJENT`: the collector could not read the rule; the household confirms the usual one.
UNKNOWN: Final = "UKJENT"

#: Fjellnett's month factors for `FEM_VEKTET_ÅR` (Nettleieforklaring og
#: fellesbestemmelser 2026): a week weighs as its Monday's month.
FJELLNETT_WEIGHTS: Final = (
    (1, 1.0),
    (2, 1.0),
    (3, 0.85),
    (4, 0.5),
    (5, 0.3),
    (6, 0.25),
    (7, 0.25),
    (8, 0.25),
    (9, 0.3),
    (10, 0.45),
    (11, 0.7),
    (12, 0.95),
)

#: `dager` → weekdays (0 = Monday) and how a holiday counts (D2 §2). `fridag` is a
#: weekend day or a holiday, which "a holiday counts as Sunday" says exactly;
#: `helligdager` alone - holidays and no Sundays - has no filter, so it is refused.
_DAYS: Final[Mapping[str, tuple[tuple[int, ...] | None, HolidayMode]]] = {
    "alle": (None, HolidayMode.IGNORE),
    "ukedag": ((0, 1, 2, 3, 4), HolidayMode.IGNORE),
    "virkedag": ((0, 1, 2, 3, 4), HolidayMode.EXCLUDE),
    "helg": ((5, 6), HolidayMode.IGNORE),
    "fridag": ((5, 6), HolidayMode.AS_SUNDAY),
    "mandag": ((0,), HolidayMode.IGNORE),
    "tirsdag": ((1,), HolidayMode.IGNORE),
    "onsdag": ((2,), HolidayMode.IGNORE),
    "torsdag": ((3,), HolidayMode.IGNORE),
    "fredag": ((4,), HolidayMode.IGNORE),
    "lørdag": ((5,), HolidayMode.IGNORE),
    "søndag": ((6,), HolidayMode.IGNORE),
}
_MONTHS: Final = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)
_MINUTES_PER_HOUR: Final = 60
_HOURS_PER_DAY: Final = 24
_MONTHS_PER_YEAR: Final = 12
#: A yearly step fee over twelve months, kept to 1/100 øre: exact in `entry.data`.
_MONTHLY: Final = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class _Rule:
    """What a version's `fastledd` resolved to: a capacity rule, or a fee by fuse."""

    rules: tuple[TariffRule, ...]
    fee: FeeVersion | None = None


# --------------------------------------------------------------------------- #
# Operators and products
# --------------------------------------------------------------------------- #


def _group(doc: Mapping[str, Any], group: str) -> list[Mapping[str, Any]]:
    return [tariff for tariff in doc.get("tariffer", ()) if group in tariff.get("kundegrupper", ())]


def products(doc: Mapping[str, Any]) -> tuple[Product, ...]:
    """Return the customer groups priced apart - bolig and hytte (O12) - or none.

    Most companies bill a cabin as a home; only where the file prices `fritid`
    with tariffs of its own does the household choose which it is.
    """
    homes = [id(tariff) for tariff in _group(doc, HOUSEHOLD)]
    cabins = [id(tariff) for tariff in _group(doc, CABIN)]
    if not cabins or homes == cabins:
        return ()
    return (Product(HOUSEHOLD, "Bolig"), Product(CABIN, "Hytte og fritidsbolig"))


def operators(
    documents: Mapping[str, Mapping[str, Any]],
    organisations: Mapping[str, str],
    counties: Mapping[str, tuple[tuple[str, str], ...]],
) -> list[Operator]:
    """Return every company with a household tariff, keyed by its file's stem.

    `organisations` maps a GLN to its organisation number (Elhub, in the same
    tarball); `counties` is NVE's, by organisation number. A company NVE does not
    list has no zones here, and its zone is then the postcode's or the national one.
    """
    found: list[Operator] = []
    for stem, doc in documents.items():
        if not _group(doc, HOUSEHOLD):
            continue
        served: dict[str, str] = {}
        for gln in doc.get("gln") or ():
            served.update(counties.get(organisations.get(str(gln), ""), ()))
        zones = tuple(sorted(set(served.values())))
        found.append(
            Operator(
                key=stem,
                name=str(doc["netteier"]),
                products=products(doc),
                zones=zones if len(zones) > 1 else (),
                counties=tuple(sorted(served.items())) if len(zones) > 1 else (),
            )
        )
    return sorted(found, key=lambda operator: operator.name)


# --------------------------------------------------------------------------- #
# One company's tariff
# --------------------------------------------------------------------------- #


def parse(
    stem: str,
    doc: Mapping[str, Any],
    *,
    product: str | None,
    fetched: date,
    answers: Mapping[str, Any],
) -> Fetched:
    """Return one company's household (or cabin) tariff as the copy (D13 §5.11).

    Every version the file lists is kept, one that starts after `fetched`
    included: the company published it. `answers` fill what the file leaves out.
    """
    group = product or HOUSEHOLD
    rows = sorted(_group(doc, group), key=lambda tariff: str(tariff["gyldig_fra"]))
    if not rows:
        msg = f"{stem}: no {group} tariff"
        raise QualityError(msg)
    questions: list[Question] = []
    capacity: list[TariffVersion] = []
    energy: list[EnergyVersion] = []
    fees: list[FeeVersion] = []
    for tariff in rows:
        valid_from = date.fromisoformat(str(tariff["gyldig_fra"]))
        rule = _fastledd(stem, tariff["fastledd"], valid_from, answers, questions)
        capacity.append(
            TariffVersion(
                valid_from=valid_from,
                version_id=f"no.{stem}.{KEY}@{valid_from.isoformat()}",
                rules=rule.rules,
                verified=fetched.isoformat(),
                source_url=_source(doc),
            )
        )
        if rule.fee is not None:
            fees.append(rule.fee)
        energy.append(_energy(stem, tariff["energiledd"], valid_from))
    last = rows[-1].get("gyldig_til")
    valid_to = None if not last else date.fromisoformat(str(last)) - timedelta(days=1)
    _stale(doc, fetched, capacity, valid_to, questions)
    grid = GridTariff(
        operator=str(doc["netteier"]),
        product=None if product is None else ("Hytte" if group == CABIN else "Bolig"),
        provenance=Provenance(
            source=KEY, url=_source(doc), fetched=fetched, attribution=ATTRIBUTION, tier="T1b"
        ),
        currency="NOK",
        basis=EXCL,
        capacity=tuple(capacity),
        energy=tuple(energy),
        fixed_fee=tuple(fees),
        capacity_id=f"no.{stem}.{KEY}",
        valid_to=valid_to,
        operator_key=stem,
        product_key=product,
    )
    return Fetched(grid=grid, questions=_unique(q for q in questions if q.key not in answers))


def _source(doc: Mapping[str, Any]) -> str:
    return str((doc.get("kilder") or [REPOSITORY])[0])


def _unique(questions: Iterable[Question]) -> tuple[Question, ...]:
    """Each gap once; one the household already answered is not asked again."""
    seen: dict[str, Question] = {}
    for question in questions:
        seen.setdefault(question.key, question)
    return tuple(seen.values())


def _stale(
    doc: Mapping[str, Any],
    fetched: date,
    capacity: Sequence[TariffVersion],
    valid_to: date | None,
    questions: list[Question],
) -> None:
    """§5.4's guard: an old file, or none valid today, is confirmed against the bill."""
    checked = date.fromisoformat(str(doc.get("sist_oppdatert") or "1970-01-01"))
    current = capacity[0].valid_from <= fetched and (valid_to is None or fetched <= valid_to)
    if fetched - checked > STALE_AFTER or not current:
        questions.append(
            Question(
                "checked",
                True,
                f"Fri Nettleie last checked these prices on {checked.isoformat()}; "
                "compare them with your latest grid bill.",
            )
        )


def _fastledd(
    stem: str,
    fastledd: Mapping[str, Any],
    valid_from: date,
    answers: Mapping[str, Any],
    questions: list[Question],
) -> _Rule:
    method = str(fastledd.get("metode"))
    if method == UNKNOWN:
        questions.append(
            Question(
                "method",
                next(iter(ANSWERS)),
                "Fri Nettleie does not say how this company measures your capacity step; "
                "the usual rule is the mean of the month's three highest hours on different days.",
                tuple(ANSWERS),
            )
        )
        answer = str(answers.get("method", next(iter(ANSWERS))))
        method = ANSWERS.get(answer, answer)
    if method not in METHODS:
        msg = f"{stem}: method {method!r} is not one PowerPlan knows"
        raise QualityError(msg)
    inclusive = fastledd.get("terskel_inkludert")
    if inclusive is None:
        questions.append(
            Question(
                "inclusive",
                True,
                "Fri Nettleie does not say whether a peak exactly on a step's bound "
                "belongs to the step above; most companies say it does.",
            )
        )
        inclusive = bool(answers.get("inclusive", True))
    thresholds = sorted(fastledd["terskler"], key=lambda row: float(row["terskel"]))
    if not thresholds or float(thresholds[0]["terskel"]) != 0:
        msg = f"{stem}: the step table must start at 0"
        raise QualityError(msg)
    if method == "OV_TREFASE":
        return _Rule(rules=(NoPeak(),), fee=_fuse_fee(thresholds, valid_from, answers, questions))
    steps = StepTable(steps=_steps(thresholds), inclusive=bool(inclusive))
    return _Rule(rules=(_peak(method, steps),))


def _steps(thresholds: Sequence[Mapping[str, Any]]) -> tuple[Step, ...]:
    """Return lower bounds and yearly prices as D2's steps: upper bounds, NOK per month."""
    rows: list[Step] = []
    for index, row in enumerate(thresholds):
        upper = float(thresholds[index + 1]["terskel"]) if index + 1 < len(thresholds) else None
        lower = float(row["terskel"])
        name = f"{lower:g}–{upper:g} kW" if upper is not None else f"over {lower:g} kW"
        fee = (Decimal(str(row["pris"])) / _MONTHS_PER_YEAR).quantize(_MONTHLY)
        rows.append(Step(upper_kw=upper, fee_per_period=Money(fee, "NOK"), name=name))
    return tuple(rows)


def _peak(method: str, steps: StepTable) -> PeakTariff:
    if method == "MND_MAX":
        return PeakTariff(
            window_min=60,
            eligible=None,
            weights=(),
            per_day="max",
            per_period="max",
            period="month",
            pricing=steps,
        )
    if method == "FEM_VEKTET_ÅR":
        return PeakTariff(
            window_min=60,
            eligible=None,
            weights=tuple(
                WeightRule(when=TimeFilter(months=(month,)), weight=weight)
                for month, weight in FJELLNETT_WEIGHTS
            ),
            per_day="max",
            per_period="mean_top_n",
            n=5,
            period="rolling_months",
            rolling_months=12,
            pricing=steps,
            group="week",
        )
    return PeakTariff(
        window_min=60,
        eligible=None,
        weights=(),
        per_day="max",
        per_period="mean_top_n",
        n=3,
        distinct_days=True,
        period="month",
        pricing=steps,
    )


def _fuse_fee(
    thresholds: Sequence[Mapping[str, Any]],
    valid_from: date,
    answers: Mapping[str, Any],
    questions: list[Question],
) -> FeeVersion:
    """`OV_TREFASE`: the yearly fee by the main fuse's amps (Alut's rule, G8)."""
    fuse = answers.get("main_fuse_a")
    if fuse is None:
        questions.append(
            Question("main_fuse_a", 25, "This company prices by the size of your main fuse.")
        )
        fuse = 25
    chosen = thresholds[0]
    for row in thresholds:
        if float(row["terskel"]) <= float(fuse):
            chosen = row
    return FeeVersion(valid_from=valid_from, amount=Decimal(str(chosen["pris"])), per="year")


def _energy(stem: str, energiledd: Mapping[str, Any], valid_from: date) -> EnergyVersion:
    """Return the energy charge: the exceptions first, the base price for every other hour."""
    periods = [
        EnergyPeriod(
            when=_filter(stem, exception),
            price=_nok(exception["pris"]),
            name=str(exception.get("navn") or "unntak").lower(),
        )
        for exception in energiledd.get("unntak") or ()
    ]
    periods.append(EnergyPeriod(when=None, price=_nok(energiledd["grunnpris"]), name="grunnpris"))
    return EnergyVersion(valid_from=valid_from, periods=tuple(periods))


def _nok(ore: Any) -> Decimal:
    """Return øre as NOK, the major unit D1 prices in."""
    return Decimal(str(ore)) / 100


def _filter(stem: str, exception: Mapping[str, Any]) -> TimeFilter:
    weekdays, holidays = _days(stem, exception.get("dager") or ["alle"])
    months = exception.get("måneder")
    hours = exception.get("timer")
    return TimeFilter(
        months=None if not months else tuple(_MONTHS.index(month) + 1 for month in months),
        weekdays=weekdays,
        hours=None if hours is None else _hours(stem, hours),
        holidays=holidays,
    )


def _hours(stem: str, value: Any) -> tuple[tuple[int, int], ...]:
    """Return `16-21` - 16:00 to 21:59, the upper hour included - and lists of them."""
    items = value if isinstance(value, list) else [value]
    spans: list[tuple[int, int]] = []
    for item in items:
        text = str(item)
        try:
            first, last = (
                (int(part) for part in text.split("-")) if "-" in text else (int(text),) * 2
            )
        except ValueError as err:
            msg = f"{stem}: hours {text!r} are not 'first-last'"
            raise QualityError(msg) from err
        end = ((last + 1) % _HOURS_PER_DAY) * _MINUTES_PER_HOUR
        spans.append((first * _MINUTES_PER_HOUR, end))
    return tuple(spans)


def _days(stem: str, days: Sequence[str]) -> tuple[tuple[int, ...] | None, HolidayMode]:
    """Return the union of `dager` - they read as "or" - as one filter, or refuse it."""
    weekdays: set[int] = set()
    rules: set[HolidayMode] = set()
    for day in days:
        if day not in _DAYS:
            msg = f"{stem}: days {day!r} have no filter that says exactly them"
            raise QualityError(msg)
        chosen, rule = _DAYS[day]
        if chosen is None:
            return None, HolidayMode.IGNORE
        weekdays |= set(chosen)
        rules.add(rule)
    if len(rules) > 1:
        msg = f"{stem}: days {list(days)!r} mix how a holiday counts"
        raise QualityError(msg)
    return tuple(sorted(weekdays)), rules.pop()
