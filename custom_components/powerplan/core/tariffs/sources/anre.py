"""Romania's grid tariffs from ANRE's offer comparator (D13 §5.9; T1a).

The regulator's consumer site (posf.ro) asks its own API, open, no key:
`get-judete` names each county's distribution zone (8), `comparator-electric`
answers every household offer in a zone at low voltage, each carrying the zone's
regulated lines in lei/kWh excl. VAT: distribution (`tarif_serviciu_distributie`),
transport (`tarif_transport_tl`), system service (`tarif_serviciu_sistem`) - the
grid party - and the state's cogeneration contribution, green certificates and
excise, which are the RO module's levies (D-0603).

The zone's lines are the same in every offer; the copy takes the most common and
fails closed where the offers name none. Pure.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import EXCL, EnergyVersion, GridTariff, Provenance
from ..model import NoPeak, TariffVersion
from .base import Fetched, Operator, QualityError, slug

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "COUNTIES",
    "KEY",
    "LEVIES",
    "disagreements",
    "levies",
    "offers_url",
    "operators",
    "parse",
]

KEY: Final = "anre"
API: Final = "https://posf.ro/comparator/api/index.php"
COUNTIES: Final = f"{API}?request=get-judete"
PAGE: Final = "https://posf.ro/comparator"
ATTRIBUTION: Final = "ANRE — Autoritatea Națională de Reglementare în Domeniul Energiei"
#: The grid party's lines, in the order the invoice gives them.
GRID_LINES: Final = ("tarif_serviciu_distributie", "tarif_transport_tl", "tarif_serviciu_sistem")
#: The state's lines by the RO module's levy keys.
LEVIES: Final = {
    "cogenerare": "taxa_cogenerare_inalta_eficienta",
    "certificate_verzi": "contravaloare_certificate_verzi",
    "acciza": "acciza",
}


def offers_url(zone: str, day: date) -> str:
    """Return the query the comparator's page sends for a household at low voltage."""
    return (
        f"{API}?request=comparator-electric&tip_oferta=0&data_start_aplicare={day.isoformat()}"
        "&tip_client=casnic&tip_pret=nediferentiat&consum_anual=2400&consum_lunar=200"
        f"&valoare_factura_curenta=&nivel_tensiune=JT_&id_zona={zone}&tip_produs=0"
        "&perioada_contract=&energie_regenerabila=&factura_electronica="
        "&frecventa_emitere_factura=&procent_zona_noapte=&procent_zona_zi="
        "&frecventa_citire_contor=&valoare_fixa="
    )


def _json(document: bytes) -> Any:
    try:
        return json.loads(document)
    except ValueError as err:
        msg = f"{KEY}: not JSON: {err}"
        raise QualityError(msg) from err


def operators(counties: bytes) -> list[Operator]:
    """Return the eight distribution zones, each named with its counties."""
    zones: dict[str, tuple[str, list[str]]] = {}
    for row in _json(counties):
        zone = zones.setdefault(str(row["id_zona"]), (str(row["nume_zona"]), []))
        zone[1].append(str(row["nume"]))
    return [
        Operator(key, f"{name} ({', '.join(sorted(members))})")
        for key, (name, members) in sorted(zones.items(), key=lambda item: item[1][0])
    ]


def _mode(offers: list[dict[str, Any]], fields: tuple[str, ...]) -> tuple[str, ...]:
    found = Counter(
        tuple(str(offer.get(field)) for field in fields)
        for offer in offers
        if str(offer.get("unitate_masura")) == "lei/kWh"
        and all(offer.get(field) not in (None, "") for field in fields)
    )
    if not found:
        msg = f"{KEY}: no offer names the zone's {', '.join(fields)}"
        raise QualityError(msg)
    return found.most_common(1)[0][0]


def levies(document: bytes) -> dict[str, Decimal]:
    """Return the state's lines the zone's offers state most often, by the module's keys."""
    values = _mode(list(_json(document)), tuple(LEVIES.values()))
    return {key: Decimal(value) for key, value in zip(LEVIES, values, strict=True)}


def parse(document: bytes, zone: str, *, name: str, fetched: date, url: str) -> Fetched:
    """Return one zone's grid lines as the copy, per kWh excl. VAT."""
    offers = list(_json(document))
    lines = _mode(offers, GRID_LINES)
    per_kwh = sum((Decimal(value) for value in lines), Decimal(0))
    since = date(fetched.year, 1, 1)
    key = slug("ro", zone, KEY)
    grid = GridTariff(
        operator=name,
        product="Joasă tensiune",
        provenance=Provenance(
            source=KEY, url=PAGE, fetched=fetched, attribution=ATTRIBUTION, tier="T1a"
        ),
        currency="RON",
        basis=EXCL,
        capacity=(
            TariffVersion(
                valid_from=since,
                version_id=f"{key}@{since.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=url,
            ),
        ),
        energy=(EnergyVersion(valid_from=since, periods=(), fallback=per_kwh),),
        capacity_id=key,
        operator_key=zone,
    )
    return Fetched(grid=grid)


def disagreements(stated: Mapping[str, Decimal], module: Mapping[str, Decimal]) -> list[str]:
    """Return the levies where the comparator and the RO module differ (the canary)."""
    return [
        f"{key}: comparator {value}, module {module.get(key)}"
        for key, value in stated.items()
        if module.get(key) != value
    ]
