"""Poland: Tauron Dystrybucja's rate card from its calculator page (D13 §5.10; T4).

`taniej.tauron-dystrybucja.pl` embeds the whole household card as JSON,
`electricCalculatorConfig.stawki`: one row per charge - fixed per phase
(`Stała`), variable per zone (`Zmienna`), subscription, quality, transition,
OZE, cogeneration (`KOG`) and the capacity fee by consumption group (`Mocowa`) -
one column per tariff and zone, in PLN **incl. VAT** (the card's own `VAT`
row, 23 %, confirmed against the net figures, D-0606).

The copy is the grid party: the variable and quality rates per zone, published
with VAT and with OZE and KOG inside (the PL module's levies); the fixed,
subscription, transition and capacity fees a month. The zones' hours are the
page's own sentences, checked on every fetch (a changed sentence fails closed).
G13's hours are an image and G14dynamic's are PSE's day-ahead (G19): neither
is offered yet. Pure.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import Basis, EnergyPeriod, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import HolidayMode, NoPeak, TariffVersion, TimeFilter
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["KEY", "PAGE", "PRODUCTS", "card", "operators", "parse"]

KEY: Final = "tauron"
PAGE: Final = "https://taniej.tauron-dystrybucja.pl/"
ATTRIBUTION: Final = "TAURON Dystrybucja S.A."
PRODUCTS: Final = (
    Product("g11", "G11 — jednostrefowa"),
    Product("g12", "G12 — dwustrefowa (dzień / noc)"),
    Product("g12w", "G12w — dwustrefowa z weekendami"),
)
GROUPS: Final = ("I", "II", "III", "IV")
_MARKER: Final = "electricCalculatorConfig = "
#: The page's own sentences for the zones' hours, whitespace-normalised.
_G12_LOW: Final = "Niższa stawka w godzinach: 22:00-6:00 oraz 13:00-15:00"
_G12W_LOW: Final = (
    "Niższa stawka w dni robocze w godzinach 13:00-15:00 oraz 22:00-6:00, a także całą "
    "sobotę i niedzielę oraz dni ustawowo wolne od pracy"
)
_LOW_HOURS: Final = ((22 * 60, 6 * 60), (13 * 60, 15 * 60))
_WORKDAYS: Final = (0, 1, 2, 3, 4)


def card(page: bytes) -> dict[str, dict[str, Any]]:
    """Return the page's rate card, rows by title."""
    text = page.decode("utf-8", "replace")
    start = text.find(_MARKER)
    if start < 0:
        msg = f"{KEY}: the page no longer embeds electricCalculatorConfig"
        raise QualityError(msg)
    try:
        config, _ = json.JSONDecoder().raw_decode(text[start + len(_MARKER) :])
    except ValueError as err:
        msg = f"{KEY}: electricCalculatorConfig is not JSON: {err}"
        raise QualityError(msg) from err
    return {str(row["title"]): row for row in config.get("stawki") or ()}


def _sentences(page: bytes) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", page.decode("utf-8", "replace")))
    return " ".join(text.replace("\xa0", " ").split())


def operators(page: bytes) -> list[Operator]:
    """Return Tauron Dystrybucja with the tariffs whose hours the page states."""
    card(page)
    return [Operator("tauron", ATTRIBUTION, products=PRODUCTS)]


def _value(rows: Mapping[str, Mapping[str, Any]], title: str, column: str) -> Decimal:
    try:
        return Decimal(str(rows[title][column]))
    except KeyError as err:
        msg = f"{KEY}: the card has no {title!r} for {column}"
        raise QualityError(msg) from err


def _periods(page: bytes, product: str, prices: Mapping[str, Decimal]) -> tuple[EnergyPeriod, ...]:
    if product == "g11":
        return ()
    text = _sentences(page)
    sentence = _G12_LOW if product == "g12" else _G12W_LOW
    if sentence not in text:
        msg = f"{KEY}: the page no longer says {sentence!r}"
        raise QualityError(msg)
    low = prices["strefa2"]
    if product == "g12":
        return (EnergyPeriod(TimeFilter(hours=_LOW_HOURS), low, "noc"),)
    return (
        # A public holiday reads as a Sunday: "dni ustawowo wolne od pracy".
        EnergyPeriod(TimeFilter(weekdays=(5, 6), holidays=HolidayMode.AS_SUNDAY), low, "weekend"),
        EnergyPeriod(TimeFilter(weekdays=_WORKDAYS, hours=_LOW_HOURS), low, "noc"),
    )


def parse(page: bytes, product: str, *, fetched: date, answers: Mapping[str, Any]) -> Fetched:
    """Return one tariff's copy, as published: incl. VAT, OZE and KOG inside the rate."""
    rows = card(page)
    if product not in {p.key for p in PRODUCTS}:
        msg = f"{KEY}: {product} is not offered"
        raise QualityError(msg)
    vat = _value(rows, "VAT", "g11_1faz")
    phases = int(answers.get("phases", 3))
    group = str(answers.get("group", "IV"))
    if group not in GROUPS:
        msg = f"{KEY}: no consumption group {group!r}"
        raise QualityError(msg)
    questions = [
        Question(
            "phases",
            3,
            "Your connection's phases (1 or 3): the fixed fee is per phase count.",
            ("1", "3"),
        ),
        Question(
            "group",
            "IV",
            "Your yearly consumption group for the capacity fee: I < 500 kWh, II 500–1 200, "
            "III 1 200–2 800, IV > 2 800.",
            GROUPS,
        ),
    ]
    zones = ("strefa1", "strefa2") if product != "g11" else ("1faz",)
    shared = _value(rows, "Jakościowa", "g11_1faz")
    prices = {zone: _value(rows, "Zmienna", f"{product}_{zone}") + shared for zone in zones}
    column = f"{product}_{zones[0]}" if product != "g11" else f"g11_{phases}faz"
    fixed = _value(rows, "Stała 3 fazy" if phases == 3 else "Stała 1 faza", column)  # noqa: PLR2004
    monthly = (
        fixed + _value(rows, "Abonament", column) + _value(rows, f"Mocowa {group} grupa", column)
    )
    oze_kog = _value(rows, "OZE", column) + _value(rows, "KOG", column)
    since = date(fetched.year, 1, 1)
    key = slug("pl", "tauron", product, KEY)
    fallback = prices[zones[0]] + oze_kog
    grid = GridTariff(
        operator=ATTRIBUTION,
        product=next(p.name for p in PRODUCTS if p.key == product),
        provenance=Provenance(
            source=KEY, url=PAGE, fetched=fetched, attribution=ATTRIBUTION, tier="T4"
        ),
        currency="PLN",
        basis=Basis(vat=True, levies=frozenset({"oze", "kog"})),
        capacity=(
            TariffVersion(
                valid_from=since,
                version_id=f"{key}@{since.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=PAGE,
            ),
        ),
        energy=(
            EnergyVersion(
                valid_from=since,
                periods=tuple(
                    EnergyPeriod(p.when, p.price + oze_kog, p.name)
                    for p in _periods(page, product, prices)
                ),
                fallback=fallback,
            ),
        ),
        fixed_fee=(FeeVersion(valid_from=since, amount=monthly, per="month"),),
        capacity_id=key,
        operator_key="tauron",
        product_key=product,
    )
    if vat != Decimal("0.23"):
        msg = f"{KEY}: the card is priced at VAT {vat}, not 23 %"
        raise QualityError(msg)
    return Fetched(grid=grid, questions=tuple(q for q in questions if q.key not in answers))
