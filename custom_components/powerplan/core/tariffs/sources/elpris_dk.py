"""Denmark's grid tariffs from elpris.dk, the regulator's price site (D13 §5.9; T1a).

Forsyningstilsynet's elpris.dk loads three documents any visitor's browser loads (§5.2):
`static.json` - every postcode's grid areas (1 073 postcodes, 34 areas),
`distributionAreaCharge_{area}.json` - the area's C tariff hour by hour and its
subscription per month, and `nationalCharges.json` - Energinet's system and transmission
tariffs per kWh and its subscription per month, and the state's elafgift. All excl. VAT.
Elafgift is the DK module's (INV-70, INV-72); the rest is the copy. elpris.dk publishes
the tariff in force only; the next season, once registered, comes from Energinet's
Datahub (D-0571). Pure.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from ..household import EXCL, FeeVersion, GridTariff, Provenance
from ..model import NoPeak, TariffVersion
from . import datahub_pricelist
from .base import Fetched, Operator, QualityError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "AREA",
    "KEY",
    "NATIONAL",
    "STATIC",
    "areas_for",
    "charge_code",
    "operators",
    "owner",
    "parse",
]

KEY: Final = "elpris_dk"
_BASE: Final = "https://elpris.dk/data/"
STATIC: Final = f"{_BASE}static.json"
NATIONAL: Final = f"{_BASE}nationalCharges.json"
AREA: Final = _BASE + "distributionAreaCharge_{area}.json"
ATTRIBUTION: Final = "elpris.dk (Forsyningstilsynet) and Energinet"
#: Energinet's charges per kWh that every household pays through its grid company.
_PER_KWH: Final = frozenset({"systemTariff", "transmissionTariff"})
_PER_MONTH: Final = frozenset({"tsoAbonnement"})


def _areas(static: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for postcode in static.get("zipCodes") or ():
        for area in postcode.get("distributionAreas") or ():
            key = str(area["gridArea"])
            found.setdefault(
                key, (str(area["name"]).strip(), str(area.get("gridCompanyName") or "").strip())
            )
    return found


def operators(static: Mapping[str, Any]) -> list[Operator]:
    """Return every grid area, keyed by its number, named as elpris.dk names it."""
    return sorted(
        (Operator(key=key, name=name) for key, (name, _) in _areas(static).items()),
        key=lambda operator: operator.name,
    )


def owner(static: Mapping[str, Any], area: str) -> str | None:
    """Return the company that owns an area, as Datahub names charge owners."""
    found = _areas(static).get(area)
    return None if found is None else found[1] or found[0]


def areas_for(static: Mapping[str, Any], postcode: str) -> list[str]:
    """Return a postcode's grid areas (two where the boundary runs through it)."""
    for entry in static.get("zipCodes") or ():
        if str(entry.get("zipCode")) == postcode.strip():
            return [str(area["gridArea"]) for area in entry.get("distributionAreas") or ()]
    return []


def charge_code(area_doc: Mapping[str, Any]) -> str | None:
    """Return the code of the area's newest tariff - Datahub's `ChargeTypeCode`."""
    tariffs = sorted(
        (
            c
            for c in area_doc.get("distributionAreaCharges") or ()
            if c.get("chargeType") == "tariff"
        ),
        key=lambda charge: str(charge["validFrom"]),
    )
    return str(tariffs[-1]["chargeId"]) if tariffs else None


def parse(
    area_doc: Mapping[str, Any],
    national: Mapping[str, Any],
    future: Sequence[Mapping[str, Any]],
    *,
    area: str,
    name: str,
    fetched: date,
) -> Fetched:
    """Return one area's copy: the tariff in force, and the seasons Datahub adds."""
    charges = list(area_doc.get("distributionAreaCharges") or ())
    tariffs = sorted(
        (c for c in charges if c.get("chargeType") == "tariff"),
        key=lambda charge: str(charge["validFrom"]),
    )
    if not tariffs:
        msg = f"{KEY}: area {area} publishes no tariff"
        raise QualityError(msg)
    extra = sum(
        (
            Decimal(str(row["amount"]))
            for row in national.get("nationalCharges") or ()
            if row.get("chargeType") in _PER_KWH
        ),
        Decimal(0),
    )
    energy = []
    last: date | None = None
    for tariff in tariffs:
        hours = {
            int(row["hoursFrom"]): Decimal(str(row["amount"]))
            for row in tariff["distributionAreaChargeHours"]
        }
        if sorted(hours) != list(range(datahub_pricelist.HOURS)):
            msg = f"{KEY}: area {area}'s tariff does not price every hour"
            raise QualityError(msg)
        since = date.fromisoformat(str(tariff["validFrom"]))
        energy.append(
            datahub_pricelist.hourly(
                since, [hours[h] + extra for h in range(datahub_pricelist.HOURS)]
            )
        )
        last = date.fromisoformat(str(tariff["validTo"])) if tariff.get("validTo") else None
    code = str(tariffs[-1]["chargeId"])
    for version, until in datahub_pricelist.versions(future, code, extra):
        if version.valid_from > energy[-1].valid_from:
            energy.append(version)
            last = until
    monthly = sum(
        (Decimal(str(c["price"])) for c in charges if c.get("chargeType") == "subscription"),
        Decimal(0),
    ) + sum(
        (
            Decimal(str(row["amount"]))
            for row in national.get("nationalCharges") or ()
            if row.get("chargeType") in _PER_MONTH
        ),
        Decimal(0),
    )
    url = AREA.format(area=area)
    grid = GridTariff(
        operator=name,
        product=None,
        provenance=Provenance(
            source=KEY, url=url, fetched=fetched, attribution=ATTRIBUTION, tier="T1a"
        ),
        currency="DKK",
        basis=EXCL,
        capacity=tuple(
            TariffVersion(
                valid_from=version.valid_from,
                version_id=f"dk.{area}.{KEY}@{version.valid_from.isoformat()}",
                rules=(NoPeak(),),
                verified=fetched.isoformat(),
                source_url=url,
            )
            for version in energy
        ),
        energy=tuple(energy),
        fixed_fee=(FeeVersion(valid_from=energy[0].valid_from, amount=monthly, per="month"),),
        capacity_id=f"dk.{area}.{KEY}",
        valid_to=None if last is None else last - timedelta(days=1),
        operator_key=area,
        product_key=None,
    )
    return Fetched(grid=grid)
