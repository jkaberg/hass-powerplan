"""Sweden's grid tariffs from Ei's household file (D13 §5.5; T6, D-0569).

Energimarknadsinspektionen publishes every grid company's household tariffs as
one workbook, `Hushållskunder.xlsx`, yearly since 1999: per company and network
area (`Gruppnamn`), five typical customers - a flat on 16 A and houses on 16, 20
and 25 A - each with the authority fees and the fixed fee in SEK per year and one
or two energy rates in öre per kWh, all **excl. VAT** and without the energy
tax. The file has **no power fee** and no hours for a second rate: the copy asks
the household to confirm there is no power fee on its bill, and the hours of the
first rate where there are two (§5.6 rule 8). A company that bills a power fee is
Eltariff's (T1a) or the rule template's.

Read with the standard library - the workbook is a zip of XML - and pure: the
file's bytes in, a copy out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import EXCL, EnergyPeriod, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import HolidayMode, NoPeak, TariffVersion, TimeFilter
from . import xlsx
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["KEY", "PAGE", "PRODUCTS", "Row", "operators", "parse", "read", "workbook_url"]

KEY: Final = "ei_household"
#: Ei's page for the workbooks; the file's own URL changes with each edition.
PAGE: Final = "https://ei.se/om-oss/statistik-och-oppna-data/natavgifter---elnat"
ATTRIBUTION: Final = "Energimarknadsinspektionen (Ei), Hushållskunder"
_LINK: Final = re.compile(r'href="(/download/[^"]+/Hush%C3%A5llskunder\.xlsx)"')

#: The file's customer groups: column code prefix, product key, name.
PRODUCTS: Final = (
    ("NT16", "lgh16", "Lägenhet 16 A"),
    ("NT20", "villa16", "Villa 16 A"),
    ("NT30", "villa20", "Villa 20 A"),
    ("NT50", "villa25", "Villa 25 A"),
)
_OREN_PER_KRONA: Final = 100
#: The file's first year a copy keeps: the one before the fetch's.
_YEARS_KEPT: Final = 2


@dataclass(frozen=True, slots=True)
class Row:
    """One company's network area: its identity and its figures by column code and year."""

    company_id: str
    company: str
    area: str
    figures: Mapping[tuple[str, int], Decimal]


def workbook_url(page: bytes) -> str:
    """Return the current workbook's URL from Ei's page; `QualityError` when it moved."""
    found = _LINK.search(page.decode("utf-8", "replace"))
    if found is None:
        msg = f"{KEY}: Ei's page links no Hushållskunder.xlsx"
        raise QualityError(msg)
    return f"https://ei.se{found.group(1)}"


def read(workbook: bytes) -> list[Row]:
    """Return the workbook's rows; `QualityError` when its layout is not Ei's."""
    grid = xlsx.sheet(workbook)
    if len(grid) < 4 or grid[2].get("A") != "ReNamn":  # noqa: PLR2004 - three header rows
        msg = f"{KEY}: the workbook's header is not Ei's"
        raise QualityError(msg)
    codes, years = grid[0], grid[2]
    rows: list[Row] = []
    for cells in grid[3:]:
        figures: dict[tuple[str, int], Decimal] = {}
        for column, value in cells.items():
            code, year = codes.get(column, ""), years.get(column, "")
            if not code.startswith("NT") or not year.isdigit() or value == "":
                continue
            figures[code, int(year)] = xlsx.number(value)
        if cells.get("A"):
            rows.append(Row(cells["A"], cells.get("B", ""), cells.get("C", ""), figures))
    return rows


# --------------------------------------------------------------------------- #
# Operators and products
# --------------------------------------------------------------------------- #


def _current(rows: Sequence[Row], year: int) -> list[Row]:
    """Rows with a fixed fee for `year` in at least one customer group."""
    return [
        row for row in rows if any((f"{prefix}20", year) in row.figures for prefix, *_ in PRODUCTS)
    ]


def operators(rows: Sequence[Row], year: int, zones: tuple[str, ...] = ()) -> list[Operator]:
    """Return each company with figures for `year`; its products are area × customer group."""
    by_company: dict[str, list[Row]] = {}
    for row in _current(rows, year):
        by_company.setdefault(row.company_id, []).append(row)
    found: list[Operator] = []
    for company_id, company_rows in by_company.items():
        several = len(company_rows) > 1
        products = tuple(
            Product(
                _product_key(row, key, several),
                f"{name} – {row.area}" if several and row.area else name,
            )
            for row in company_rows
            for prefix, key, name in PRODUCTS
            if (f"{prefix}20", year) in row.figures
        )
        found.append(Operator(company_id, company_rows[0].company, products=products, zones=zones))
    return sorted(found, key=lambda operator: operator.name)


def _product_key(row: Row, key: str, several: bool) -> str:
    return f"{row.area}|{key}" if several else key


# --------------------------------------------------------------------------- #
# One product
# --------------------------------------------------------------------------- #


def parse(
    rows: Sequence[Row],
    company_id: str,
    product: str,
    *,
    fetched: date,
    url: str,
    answers: Mapping[str, Any],
) -> Fetched:
    """Return one company's customer group as the copy, one version per year from last year."""
    area, _, key = product.rpartition("|")
    prefix = next((p for p, k, _ in PRODUCTS if k == key), None)
    row = next(
        (
            r
            for r in _current(rows, fetched.year)
            if r.company_id == company_id
            and (not area or r.area == area)
            and prefix is not None
            and (f"{prefix}20", fetched.year) in r.figures
        ),
        None,
    )
    if prefix is None or row is None:
        msg = f"{KEY}: {company_id} has no {product!r}"
        raise QualityError(msg)
    questions: list[Question] = []
    if answers.get("no_power_fee") is False:
        msg = f"{KEY}: {company_id} bills a power fee Ei's file does not carry"
        raise QualityError(msg)
    questions.append(
        Question(
            "no_power_fee",
            True,
            "Ei's file has no power fee (effektavgift): check that your grid bill has none.",
        )
    )
    years = sorted(
        year
        for (code, year) in row.figures
        if code == f"{prefix}20" and year >= fetched.year - _YEARS_KEPT + 1
    )
    if not years:
        msg = f"{KEY}: {company_id} has no figures for {fetched.year}"
        raise QualityError(msg)
    capacity: list[TariffVersion] = []
    energy: list[EnergyVersion] = []
    fees: list[FeeVersion] = []
    for year in years:
        since = date(year, 1, 1)
        capacity.append(
            TariffVersion(
                valid_from=since,
                version_id=f"{slug('se', company_id, key, KEY)}@{since.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=url,
            )
        )
        fees.append(
            FeeVersion(
                valid_from=since,
                amount=row.figures.get((f"{prefix}20", year), Decimal(0))
                + row.figures.get((f"{prefix}10", year), Decimal(0)),
                per="year",
            )
        )
        energy.append(_energy(row, prefix, year, since, answers, questions))
    name = next(n for p, _, n in PRODUCTS if p == prefix)
    grid = GridTariff(
        operator=row.company,
        product=f"{name} – {row.area}" if area else name,
        provenance=Provenance(
            source=KEY, url=url, fetched=fetched, attribution=ATTRIBUTION, tier="T6"
        ),
        currency="SEK",
        basis=EXCL,
        capacity=tuple(capacity),
        energy=tuple(energy),
        fixed_fee=tuple(fees),
        capacity_id=slug("se", company_id, key, KEY),
        valid_to=date(years[-1], 12, 31),
        operator_key=company_id,
        product_key=product,
    )
    return Fetched(grid=grid, questions=tuple(q for q in questions if q.key not in answers))


def _energy(  # noqa: PLR0917 - one year of one row, and what the household said
    row: Row,
    prefix: str,
    year: int,
    since: date,
    answers: Mapping[str, Any],
    questions: list[Question],
) -> EnergyVersion:
    """One rate, or two whose first rate's hours the household confirms."""
    first = row.figures.get((f"{prefix}30", year), Decimal(0)) / _OREN_PER_KRONA
    second = row.figures.get((f"{prefix}40", year))
    if second is None:
        return EnergyVersion(valid_from=since, periods=(), fallback=first)
    questions.append(
        Question(
            "rate_1_hours",
            "06-22",
            "Ei's file gives two energy rates without their hours: when does the first apply "
            "(weekdays, hours from-to)?",
        )
    )
    questions.append(
        Question(
            "rate_1_months",
            "11-03",
            "…and in which months (from-to)?",
        )
    )
    hours = _span(str(answers.get("rate_1_hours", "06-22")), 24)
    months = _span(str(answers.get("rate_1_months", "11-03")), 12)
    when = TimeFilter(
        months=_months(months),
        weekdays=(0, 1, 2, 3, 4),
        hours=((hours[0] * 60, hours[1] * 60),),
        holidays=HolidayMode.EXCLUDE,
    )
    return EnergyVersion(
        valid_from=since,
        periods=(EnergyPeriod(when=when, price=first, name="rate 1"),),
        fallback=second / _OREN_PER_KRONA,
    )


def _span(text: str, limit: int) -> tuple[int, int]:
    """Read "06-22" or "11-03"; `QualityError` on anything else."""
    found = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", text)
    if found is None or not all(0 <= int(part) <= limit for part in found.groups()):
        msg = f"{KEY}: {text!r} is not a span like 06-22"
        raise QualityError(msg)
    return int(found.group(1)), int(found.group(2))


def _months(span: tuple[int, int]) -> tuple[int, ...]:
    first, last = span
    if first <= last:
        return tuple(range(first, last + 1))
    return (*range(first, 13), *range(1, last + 1))
