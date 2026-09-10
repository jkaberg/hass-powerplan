"""Switzerland's grid tariffs from ElCom's price site (D13 §5.10; T1a).

`strompreis.elcom.admin.ch` answers its own map's GraphQL for any visitor, no key:
`searchMunicipalities(query)` finds a postcode's municipality, `observations`
gives each grid operator in it, per consumption category (H1–H8) and year, the
price components the operators must publish by the end of August - all in
Rp./kWh excl. VAT (the site's own legend), averaged over the category's day,
night and season: the network use (`gridusage`), the municipality's and
canton's charges (`charge`), the federal grid surcharge (`aidfee`, the CH
module's levy) and the metering (`annualmeteringcost`, CHF a year).

The copy is the grid party: network use plus the local charges per kWh, the
metering as a yearly fee; ElCom publishes no HT/NT split, so the energy charge
is the category's flat average (D-0602). The energy price is the supplier's. Pure.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import EXCL, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import NoPeak, TariffVersion
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "API",
    "CATEGORIES",
    "KEY",
    "municipalities",
    "municipalities_query",
    "observations_query",
    "operators",
    "parse",
    "search_query",
]

KEY: Final = "elcom"
API: Final = "https://www.strompreis.elcom.admin.ch/api/graphql"
PAGE: Final = "https://www.strompreis.elcom.admin.ch"
ATTRIBUTION: Final = "ElCom — Eidgenössische Elektrizitätskommission"
#: ElCom's household categories, H4 first: the site's own default (`help-categories`).
CATEGORIES: Final = (
    Product("H4", "H4: 4'500 kWh/Jahr — 5-Zimmerwohnung, Elektroherd, Tumbler"),
    Product("H1", "H1: 1'600 kWh/Jahr — 2-Zimmerwohnung, Elektroherd"),
    Product("H2", "H2: 2'500 kWh/Jahr — 4-Zimmerwohnung, Elektroherd"),
    Product("H3", "H3: 4'500 kWh/Jahr — 4-Zimmerwohnung, Elektroherd, Elektroboiler"),
    Product("H5", "H5: 7'500 kWh/Jahr — Einfamilienhaus, Elektroboiler, Tumbler"),
    Product("H6", "H6: 25'000 kWh/Jahr — Einfamilienhaus, elektrische Widerstandsheizung"),
    Product("H7", "H7: 13'000 kWh/Jahr — Einfamilienhaus, Wärmepumpe 5 kW"),
    Product("H8", "H8: 7'500 kWh/Jahr — grosse, hoch elektrifizierte Eigentumswohnung"),
)
_RP_PER_CHF: Final = Decimal(100)
#: A thousandth of a Rappen per kWh, in CHF: ElCom publishes Rp. to three decimals.
_RATE: Final = Decimal("0.00001")
_FIELDS: Final = (
    "operator operatorLabel municipality municipalityLabel period category "
    "gridusage: value(priceComponent: gridusage) charge: value(priceComponent: charge) "
    "aidfee: value(priceComponent: aidfee) "
    "annualmeteringcost: value(priceComponent: annualmeteringcost)"
)


def _quote(text: str) -> str:
    return json.dumps(text)


def search_query(postcode: str) -> dict[str, str]:
    """Return the search the site's own box sends for a postcode or a name."""
    return {
        "query": f'query {{ searchMunicipalities(locale: "de", query: {_quote(postcode)}) '
        "{ id name } }"
    }


def municipalities_query() -> dict[str, str]:
    """Return the list of every municipality ElCom knows."""
    return {"query": 'query { allMunicipalities(locale: "de") { id name } }'}


def observations_query(municipality: str, category: str, year: int) -> dict[str, str]:
    """Return one municipality's operators' components for a category and a year."""
    filters = (
        f"period: [{_quote(str(year))}], municipality: [{_quote(municipality)}], "
        f"category: [{_quote(category)}]"
    )
    return {
        "query": f'query {{ observations(locale: "de", filters: {{{filters}}}) {{ {_FIELDS} }} }}'
    }


def _data(document: bytes, field: str) -> list[dict[str, Any]]:
    try:
        answer = json.loads(document)
    except ValueError as err:
        msg = f"{KEY}: not JSON: {err}"
        raise QualityError(msg) from err
    if answer.get("errors"):
        msg = f"{KEY}: {answer['errors'][0].get('message')}"
        raise QualityError(msg)
    return list((answer.get("data") or {}).get(field) or ())


def municipalities(document: bytes, *, search: bool = False) -> list[tuple[str, str]]:
    """Return `(id, name)` for a search's or the full list's municipalities."""
    field = "searchMunicipalities" if search else "allMunicipalities"
    return [(str(row["id"]), str(row["name"])) for row in _data(document, field)]


def operators(found: list[tuple[str, str]]) -> list[Operator]:
    """Return one operator per municipality: the grid operator is read with its tariff."""
    return [
        Operator(municipality, name, products=CATEGORIES)
        for municipality, name in sorted(found, key=lambda row: row[1])
    ]


def _row(
    rows: list[dict[str, Any]], answers: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[Question, ...]]:
    """Return the operator's row; where two serve the municipality, which one is asked."""
    by_operator = {str(row["operator"]): row for row in rows}
    keys = tuple(sorted(by_operator))
    questions: tuple[Question, ...] = ()
    if len(keys) > 1:
        questions = (
            Question(
                "grid_operator",
                keys[0],
                "More than one grid operator serves this municipality: "
                + ", ".join(f"{key} = {by_operator[key]['operatorLabel']}" for key in keys),
                keys,
            ),
        )
    return by_operator.get(str(answers.get("grid_operator") or "")) or by_operator[
        keys[0]
    ], questions


def parse(
    documents: Mapping[int, bytes],
    municipality: str,
    category: str,
    *,
    fetched: date,
    answers: Mapping[str, Any],
) -> Fetched:
    """Return one municipality's grid tariff for a category: a version per published year.

    `documents` are the observations by year - this year's, and next year's once
    the operators have published it (by the end of August).
    """
    versions: list[tuple[int, dict[str, Any]]] = []
    questions: tuple[Question, ...] = ()
    for year in sorted(documents):
        rows = _data(documents[year], "observations")
        if rows:
            row, questions = _row(rows, answers)
            versions.append((year, row))
    if not versions:
        msg = f"{KEY}: no {category} tariff for municipality {municipality}"
        raise QualityError(msg)
    latest = versions[-1][1]
    key = slug("ch", municipality, str(latest["operator"]), category, KEY)
    capacity, energy, fees = [], [], []
    for year, row in versions:
        if row.get("gridusage") is None:
            msg = f"{KEY}: {row['operatorLabel']} publishes no network use for {category}"
            raise QualityError(msg)
        since = date(year, 1, 1)
        per_kwh = (
            Decimal(str(row["gridusage"])) + Decimal(str(row.get("charge") or 0))
        ) / _RP_PER_CHF
        capacity.append(
            TariffVersion(
                valid_from=since,
                version_id=f"{key}@{since.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=PAGE,
            )
        )
        energy.append(EnergyVersion(valid_from=since, periods=(), fallback=per_kwh.quantize(_RATE)))
        fees.append(
            FeeVersion(
                valid_from=since,
                amount=Decimal(str(row.get("annualmeteringcost") or 0)),
                per="year",
            )
        )
    grid = GridTariff(
        operator=str(latest["operatorLabel"]),
        product=f"{latest['municipalityLabel']} · {category}",
        provenance=Provenance(
            source=KEY, url=PAGE, fetched=fetched, attribution=ATTRIBUTION, tier="T1a"
        ),
        currency="CHF",
        basis=EXCL,
        capacity=tuple(capacity),
        energy=tuple(energy),
        fixed_fee=tuple(fees),
        capacity_id=key,
        valid_to=date(versions[-1][0], 12, 31),
        operator_key=municipality,
        product_key=category,
    )
    return Fetched(grid=grid, questions=questions)
