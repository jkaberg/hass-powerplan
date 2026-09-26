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
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "AREA",
    "KEY",
    "NATIONAL",
    "STATIC",
    "areas_for",
    "charge_code",
    "charge_since",
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


def _tariffs(charges: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return the tariff records, oldest first."""
    return sorted(
        (c for c in charges if c.get("chargeType") == "tariff"),
        key=lambda charge: str(charge["validFrom"]),
    )


def charge_code(area_doc: Mapping[str, Any]) -> str | None:
    """Return the code of the area's newest tariff - Datahub's `ChargeTypeCode`."""
    tariffs = _tariffs(area_doc.get("distributionAreaCharges") or ())
    return str(tariffs[-1]["chargeId"]) if tariffs else None


def charge_since(area_doc: Mapping[str, Any]) -> date | None:
    """Return when the area's newest tariff began: where Datahub's rows are asked from."""
    tariffs = _tariffs(area_doc.get("distributionAreaCharges") or ())
    return date.fromisoformat(str(tariffs[-1]["validFrom"])) if tariffs else None


def parse(
    area_doc: Mapping[str, Any],
    national: Mapping[str, Any],
    future: Sequence[Mapping[str, Any]],
    *,
    area: str,
    name: str,
    fetched: date,
) -> Fetched:
    """Return one area's copy: the tariff in force, and the seasons Datahub adds.

    Each charge is listed as `flex`, priced by the hour, and `fix`, a flat rate for
    a meter not settled by the hour - a household's smart meter is billed `flex`.
    Where elpris.dk lists no `flex` hours, its `fix` price is only part of the
    table (Zeanet's 43110 gives the night rate), so Datahub's rows are the tariff
    (D-0682).
    """
    charges = list(area_doc.get("distributionAreaCharges") or ())
    code = charge_code(area_doc)
    # the area's own charge, billed by the hour, and only while it prices anything
    tariffs = [
        c
        for c in _tariffs(charges)
        if c["chargeId"] == code
        and c.get("billingType") == "flex"
        and not (c.get("validTo") and date.fromisoformat(str(c["validTo"])) <= fetched)
    ]
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
    unusable = f"{KEY}: area {area} publishes no hourly tariff"
    for tariff in tariffs:
        rows = tariff["distributionAreaChargeHours"]
        hours = {int(row["hoursFrom"]): Decimal(str(row["amount"])) for row in rows}
        # Elinord's 43300 is two companies' rows merged: Datahub's is the tariff
        if len(rows) != len(hours):
            unusable = f"{KEY}: area {area}'s tariff prices an hour more than once"
            continue
        if sorted(hours) != list(range(datahub_pricelist.HOURS)):
            unusable = f"{KEY}: area {area}'s tariff does not price every hour"
            continue
        since = date.fromisoformat(str(tariff["validFrom"]))
        energy.append(
            datahub_pricelist.hourly(
                since, [hours[h] + extra for h in range(datahub_pricelist.HOURS)]
            )
        )
        last = date.fromisoformat(str(tariff["validTo"])) if tariff.get("validTo") else None
    for version, until in datahub_pricelist.versions(future, str(code), extra):
        ended = until is not None and until <= fetched
        if not ended and (not energy or version.valid_from > energy[-1].valid_from):
            energy.append(version)
            last = until
    if not energy:
        raise QualityError(unusable)
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
