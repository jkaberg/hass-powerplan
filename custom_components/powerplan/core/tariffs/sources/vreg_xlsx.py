"""Flanders' grid tariffs from the Vlaamse Nutsregulator's yearly sheet (D13 §5.11; T6).

`Distributienettarieven elektriciteit <year>.xlsx`, linked from the regulator's
tariff page, has one overview sheet for low voltage (`ELEK Overzicht
laagspanning`): the eight Fluvius areas as columns, and per meter kind the
capacity rate (EUR/kW/year on the rolling mean of monthly peaks, or a fixed
term), the kWh rate in EUR/MWh and the data-management fee in EUR/year - first
excl. VAT, then incl. 6 %. The copy reads the rows excl. VAT (INV-71). The rule -
15-minute windows, the month's highest, the mean of the last twelve months, at
least 2.5 kW a month - is the regulator's; the sheet's own note on the minimum
contribution is checked on every fetch (a changed minimum fails closed, §5.6).

Two products: the digital meter (the capacity tariff) and the analogue one (a
fixed term instead). Pure.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Final

from ...model import Money
from ..household import EXCL, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import Linear, NoPeak, PeakTariff, TariffVersion
from . import xlsx
from .base import Fetched, Operator, Product, QualityError, slug

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

__all__ = ["AREAS", "KEY", "PAGE", "Sheet", "operators", "parse", "read", "workbook_url"]

KEY: Final = "vreg_xlsx"
PAGE: Final = (
    "https://www.vlaamsenutsregulator.be/elektriciteit-en-aardgas/nettarieven/"
    "hoeveel-bedragen-de-distributienettarieven"
)
ATTRIBUTION: Final = "Vlaamse Nutsregulator (VREG) and Fluvius"
OVERVIEW: Final = "ELEK Overzicht laagspanning"
#: The sheet's columns D–K, in the order of its own per-area sheets (FA … FZD).
AREAS: Final = (
    ("D", "fa"),
    ("E", "fhv"),
    ("F", "fi"),
    ("G", "fk"),
    ("H", "fl"),
    ("I", "fmv"),
    ("J", "fw"),
    ("K", "fzd"),
)
PRODUCTS: Final = (Product("digital", "Digitale meter"), Product("analog", "Analoge meter"))
_MIN_KW: Final = 2.5
_MINIMUM_NOTE: Final = re.compile(r"minimale bijdrage van 2,5 keer het capaciteitstarief")
_VALIDITY: Final = re.compile(
    r"geldt van (\d{2})/(\d{2})/(\d{4}) t\.e\.m\. (\d{2})/(\d{2})/(\d{4})"
)
_LINK: Final = re.compile(
    r'href="(https://assets\.vlaamsenutsregulator\.be/[^"]*Distributienettarieven%20elektriciteit%20(\d{4})\.xlsx[^"]*)"'
)
_KWH_PER_MWH: Final = 1000


class Sheet:
    """The overview's rows by label, per meter kind and VAT basis, and its notes."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        """Index the overview: blocks start at a VAT label with the meter kind beside it."""
        self.headers: dict[str, str] = {}
        self.blocks: dict[tuple[str, str], dict[str, dict[str, str]]] = {}
        self.notes: list[str] = []
        block: tuple[str, str] | None = None
        for cells in rows:
            if "A" in cells and "B" in cells and "btw" in cells["A"]:
                block = (cells["A"].strip(), cells["B"].strip())
                self.blocks[block] = {}
            elif block is not None and "B" in cells and "C" in cells:
                self.blocks[block][cells["B"].strip()] = cells
            if "D" in cells and "Fluvius" in cells.get("D", "") and not self.headers:
                self.headers = {col: " ".join(cells.get(col, "").split()) for col, _ in AREAS}
            if "B" in cells and "C" not in cells and not cells.get("A"):
                self.notes.append(cells["B"])

    def value(self, meter: str, label: str, column: str) -> Decimal:
        """Return one excl.-VAT figure; `QualityError` when the sheet no longer has it."""
        try:
            return xlsx.number(self.blocks["Exclusief btw", meter][label][column])
        except (KeyError, QualityError) as err:
            msg = f"{KEY}: no {label!r} for {meter} in column {column}"
            raise QualityError(msg) from err


def workbook_url(page: bytes, year: int) -> str:
    """Return the link to `year`'s electricity sheet on the regulator's page."""
    for url, found in _LINK.findall(page.decode("utf-8", "replace")):
        if int(found) == year:
            return str(url).replace("&amp;", "&")
    msg = f"{KEY}: the page links no {year} electricity sheet"
    raise QualityError(msg)


def read(workbook: bytes) -> Sheet:
    """Return the overview sheet, indexed."""
    return Sheet(xlsx.sheet(workbook, OVERVIEW))


def operators(sheet: Sheet) -> list[Operator]:
    """Return the eight Fluvius areas, each with its two meter kinds."""
    if len(sheet.headers) != len(AREAS):
        msg = f"{KEY}: the overview does not name the eight areas"
        raise QualityError(msg)
    return [Operator(key, sheet.headers[column], products=PRODUCTS) for column, key in AREAS]


def _validity(sheet: Sheet) -> tuple[date, date]:
    for note in sheet.notes:
        found = _VALIDITY.search(note)
        if found:
            d1, m1, y1, d2, m2, y2 = (int(part) for part in found.groups())
            return date(y1, m1, d1), date(y2, m2, d2)
    msg = f"{KEY}: the sheet does not say when its tariffs apply"
    raise QualityError(msg)


def parse(
    sheet: Sheet,
    area: str,
    product: str | None,
    *,
    fetched: date,
    url: str,
    names: Mapping[str, str] | None = None,
) -> Fetched:
    """Return one area's copy for a meter kind, one version for the sheet's year."""
    column = next((col for col, key in AREAS if key == area), None)
    if column is None:
        msg = f"{KEY}: no area {area!r}"
        raise QualityError(msg)
    if not any(_MINIMUM_NOTE.search(note) for note in sheet.notes):
        msg = f"{KEY}: the sheet's minimum contribution is no longer 2.5 × the rate"
        raise QualityError(msg)
    since, until = _validity(sheet)
    digital = (product or "digital") == "digital"
    meter = "Digitale meter" if digital else "Analoge meter"
    kwh = sheet.value(meter, "Totaal kWh-tarief", column) / _KWH_PER_MWH
    data = sheet.value(meter, "Tarief databeheer", column)
    fees = [FeeVersion(valid_from=since, amount=data, per="year")]
    if digital:
        rule: PeakTariff | NoPeak = PeakTariff(
            window_min=15,
            eligible=None,
            weights=(),
            per_day="all",
            per_period="max",
            period="rolling_months",
            rolling_months=12,
            price_period_unit="year",
            pricing=Linear(
                price_per_kw=Money(
                    sheet.value(meter, "Capaciteitstarief - gemiddelde maandpiek", column), "EUR"
                ),
                min_kw=_MIN_KW,
            ),
        )
    else:
        rule = NoPeak()
        fixed = sheet.value(meter, "Capaciteitstarief - vaste term", column)
        fees = [FeeVersion(valid_from=since, amount=data + fixed, per="year")]
    name = (names or sheet.headers).get(column, area)
    grid = GridTariff(
        operator=name,
        product="Digitale meter" if digital else "Analoge meter",
        provenance=Provenance(
            source=KEY, url=url, fetched=fetched, attribution=ATTRIBUTION, tier="T6"
        ),
        currency="EUR",
        basis=EXCL,
        capacity=(
            TariffVersion(
                valid_from=since,
                version_id=f"{slug('be', area, product or 'digital', KEY)}@{since.isoformat()}",
                rules=(rule,),
                verified=fetched.isoformat(),
                source_url=url,
            ),
        ),
        energy=(EnergyVersion(valid_from=since, periods=(), fallback=kwh),),
        fixed_fee=tuple(fees),
        capacity_id=slug("be", area, product or "digital", KEY),
        valid_to=until,
        operator_key=area,
        product_key=product or "digital",
    )
    return Fetched(grid=grid)
