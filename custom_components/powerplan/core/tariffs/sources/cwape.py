"""Wallonia's and Brussels' grid tariffs from the regulators' comparators (D13 §5.9; T1a).

CWaPE's CompaCWaPE and Brugel's BruSim run one platform (`api.compacwape.be`,
`api.brusim.be`): `postal_codes?code=` names a postcode's entries (a postcode can
span municipalities), `offer_simulations` - the POST their own pages send, no key
and no captcha (§5.2 rule 1) - answers every grid invoice line for a yearly
consumption on a meter type, each flagged `hasTVA`, as a yearly amount: network
usage (fixed and per kWh, per meter type), OSP, road tax, ISOC, other surcharges
and regulatory balances (the grid company's, `dnm`), and transmission (`common`).
The copy takes the rate as amount ÷ kWh and the fixed lines per year, excl. VAT.
The state's lines - excise and the energy contribution - are the BE module's and
left out (INV-72; the module does not yet carry them, D-0575).

Two meter types: single-rate (3 500 kWh on counter 1) and dual-rate (1 750 kWh
each on day and night, counters 3 and 4). The dual rate's day hours are the grid
company's rule and not in the answer: asked, 07–22 on weekdays pre-selected.
BruSim prices a connection-power segment: the site's power picks it. Pure.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import EXCL, EnergyPeriod, EnergyVersion, FeeVersion, GridTariff, Provenance
from ..model import HolidayMode, NoPeak, TariffVersion, TimeFilter
from .base import Fetched, Operator, Product, QualityError, Question, slug

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "HOSTS",
    "KEY",
    "PRODUCTS",
    "body",
    "dnm_names",
    "dnm_of",
    "operators",
    "parse",
    "postal_codes",
    "segment_for",
]

KEY: Final = "cwape"
#: The two platforms: key, API, regulator.
HOSTS: Final = (
    ("compacwape", "https://api.compacwape.be", "CWaPE"),
    ("brusim", "https://api.brusim.be", "Brugel"),
)
PRODUCTS: Final = (
    Product("single", "Compteur simple horaire"),
    Product("dual", "Compteur bi-horaire"),
)
#: Counter ids and the kWh simulated on each (the platform's own ids).
COUNTERS: Final = {"single": ((1, 3500),), "dual": ((3, 1750), (4, 1750))}
_TRANSMISSION: Final = "Transportation"
#: The rate per kWh is a yearly amount over the kWh simulated: kept to 1/10 000 cent.
_RATE: Final = Decimal("0.000001")
_FEE: Final = Decimal("0.0001")
_DAY_COUNTER: Final = 3
_NIGHT_COUNTER: Final = 4


def body(postal_id: str, product: str, segment: str | None) -> dict[str, Any]:
    """Return the simulation the comparator's page sends, for a resident, electricity only."""
    counters = COUNTERS[product]
    return {
        "gasProvider": None,
        "electricityProvider": None,
        "isElectricitySimulation": True,
        "isGasSimulation": False,
        "consumerType": "resident",
        "isProsumer": False,
        "postalCode": f"/postal_codes/{postal_id}",
        "isGasConsumptionUnknown": False,
        "gasConsumption": None,
        "gasTypicalProfile": None,
        "isElectricityConsumptionUnknown": False,
        "electricityTypicalProfile": None,
        "electricityConsumptionsPerCounterType": [
            {"counterType": counter, "consumption": kwh} for counter, kwh in counters
        ],
        "prosumerConsumptionsPerCounterType": [
            {"counterType": counter, "consumption": None} for counter, _ in counters
        ],
        "electricityConnectionPowerSegment": segment,
        "gasConnectionPowerSegment": None,
        "prosumerNetPower": None,
        "computeNetForProsumer": False,
        "hasElectricitySmartCounter": False,
        "isInjectionSeparated": False,
        "hasCompensation": False,
        "prosumerUnknownConsumptionNetPower": None,
        "prosumerUnknownConsumptionProductivity": None,
        "prosumerUnknownConsumptionAutoConsumption": None,
        "installationCertification": None,
        "clientFile": None,
        "clientHeaderFile": None,
        "nightOnly": False,
        "isTariffImpact": False,
    }


def _json(document: bytes) -> Any:
    try:
        return json.loads(document)
    except ValueError as err:
        msg = f"{KEY}: not JSON: {err}"
        raise QualityError(msg) from err


def postal_codes(document: bytes) -> list[tuple[str, str]]:
    """Return a postcode's entries: the platform's id and the municipality."""
    return [(str(entry["id"]), str(entry["municipality"])) for entry in _json(document)]


def dnm_names(document: bytes) -> dict[str, str]:
    """Return the grid companies by their resource path."""
    return {
        f"/distribution_network_managers/{entry['id']}": str(entry["name"])
        for entry in _json(document)
    }


def dnm_of(simulation: bytes) -> str:
    """Return the grid company a simulation priced; `QualityError` when it names none."""
    lines = (_json(simulation).get("electricity") or {}).get("dnm") or ()
    owners = {str((line.get("dnm") or {}).get("@id")) for line in lines}
    if len(owners) != 1:
        msg = f"{KEY}: the simulation names {len(owners)} grid companies"
        raise QualityError(msg)
    return owners.pop()


def segment_for(document: bytes, kw: float | None) -> tuple[str | None, bool]:
    """Return the connection segment for a site's power and whether it was the default."""
    segments = [s for s in _json(document) if s.get("powerType") == "electricity"]
    if kw is not None:
        for segment in segments:
            if float(segment["fromPower"]) <= kw <= float(segment["toPower"]):
                return f"/connection_power_segments/{segment['id']}", False
    default = next((s for s in segments if s.get("isDefault")), None)
    return (None if default is None else f"/connection_power_segments/{default['id']}"), True


def operators(host: str, entries: Sequence[tuple[str, str, str]]) -> list[Operator]:
    """Return one operator per grid company a postcode's entries name.

    `entries` are `(postal id, municipality, grid company)`; the first entry of
    each company is kept, and its key names the platform and the entry.
    """
    found: dict[str, Operator] = {}
    for postal_id, _, company in entries:
        found.setdefault(company, Operator(f"{host}:{postal_id}", company, products=PRODUCTS))
    return list(found.values())


def _per_counter(line: Mapping[str, Any]) -> dict[int, Decimal] | Decimal:
    price = line.get("price")
    if isinstance(price, list):
        return {int(row["counterType"]["id"]): Decimal(str(row["price"])) for row in price}
    return Decimal(str(price or 0))


def _span(text: str) -> tuple[int, int]:
    found = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", text)
    if found is None or not all(0 <= int(part) <= 24 for part in found.groups()):  # noqa: PLR2004
        msg = f"{KEY}: {text!r} is not a span like 07-22"
        raise QualityError(msg)
    return int(found.group(1)), int(found.group(2))


def parse(
    simulation: bytes,
    *,
    operator: str,
    product: str,
    company: str,
    fetched: date,
    url: str,
    answers: Mapping[str, Any],
    segment_assumed: bool = False,
) -> Fetched:
    """Return the grid lines of one simulation as the copy, excl. VAT."""
    electricity = _json(simulation).get("electricity") or {}
    lines = list(electricity.get("dnm") or ()) + [
        line
        for line in electricity.get("common") or ()
        if str(line["invoiceItem"].get("invoiceCategoryNestedName", "")).startswith(_TRANSMISSION)
    ]
    if not lines:
        msg = f"{KEY}: the simulation has no grid lines"
        raise QualityError(msg)
    counters = COUNTERS[product]
    total = Decimal(sum(kwh for _, kwh in counters))
    yearly = Decimal(0)
    shared = Decimal(0)
    per_counter = {counter: Decimal(0) for counter, _ in counters}
    for line in lines:
        item = line["invoiceItem"]
        price = _per_counter(line)
        if item.get("billingBase") == "fixed":
            yearly += price if isinstance(price, Decimal) else sum(price.values(), Decimal(0))
        elif isinstance(price, Decimal):
            shared += price / total
        else:
            for counter, kwh in counters:
                per_counter[counter] += price.get(counter, Decimal(0)) / Decimal(kwh)
    questions: list[Question] = []
    since = date(fetched.year, 1, 1)
    if product == "single":
        energy = EnergyVersion(
            valid_from=since, periods=(), fallback=(per_counter[1] + shared).quantize(_RATE)
        )
    else:
        if "rate_1_hours" not in answers:
            questions.append(
                Question(
                    "rate_1_hours",
                    "07-22",
                    "The comparator does not give the day rate's hours; your grid company's "
                    "usual are 07–22 on weekdays.",
                )
            )
        start, end = _span(str(answers.get("rate_1_hours", "07-22")))
        day = TimeFilter(
            weekdays=(0, 1, 2, 3, 4), hours=((start * 60, end * 60),), holidays=HolidayMode.EXCLUDE
        )
        energy = EnergyVersion(
            valid_from=since,
            periods=(
                EnergyPeriod(
                    when=day, price=(per_counter[_DAY_COUNTER] + shared).quantize(_RATE), name="day"
                ),
            ),
            fallback=(per_counter[_NIGHT_COUNTER] + shared).quantize(_RATE),
        )
    if segment_assumed and "connection_kw" not in answers:
        questions.append(
            Question(
                "connection_kw",
                9.6,
                "Brussels prices the connection's power: give your main fuse's kVA "
                "(the comparator's default is 6.1–9.6).",
            )
        )
    grid = GridTariff(
        operator=company,
        product=next(p.name for p in PRODUCTS if p.key == product),
        provenance=Provenance(
            source=KEY, url=url, fetched=fetched, attribution="CWaPE, Brugel", tier="T1a"
        ),
        currency="EUR",
        basis=EXCL,
        capacity=(
            TariffVersion(
                valid_from=since,
                version_id=f"{slug('be', operator, product, KEY)}@{since.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=url,
            ),
        ),
        energy=(energy,),
        fixed_fee=(FeeVersion(valid_from=since, amount=yearly.quantize(_FEE), per="year"),),
        capacity_id=slug("be", operator, product, KEY),
        operator_key=operator,
        product_key=product,
    )
    return Fetched(grid=grid, questions=tuple(questions))
