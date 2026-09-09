"""Kartverket's open address and municipality registers: a postcode's place (D13 §5.3, O17).

The postcode goes to Kartverket and nowhere else. The address search names the
municipality (`kommunenummer`); the municipality register names its county. That
settles Norway's tax zone exactly - the tiltakssone included (§9) - which a
coordinate would not (F5). Pure: the documents in, a `Place` out.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Final

from .base import Place, QualityError

__all__ = ["ADDRESSES", "MUNICIPALITY", "Place", "municipality_of", "place_of"]

ADDRESSES: Final = "https://ws.geonorge.no/adresser/v1/sok?postnummer={postcode}&treffPerSide=10"
MUNICIPALITY: Final = "https://ws.geonorge.no/kommuneinfo/v1/kommuner/{number}"


def municipality_of(addresses: bytes) -> str:
    """Return the municipality most of a postcode's addresses are in.

    A postcode may cross a municipal border; the majority settles it, and a tie
    is broken by the lower number so the answer never depends on the order.
    """
    rows = json.loads(addresses).get("adresser") or []
    counts = Counter(str(row["kommunenummer"]) for row in rows if row.get("kommunenummer"))
    if not counts:
        msg = "no address has that postcode"
        raise QualityError(msg)
    best = max(counts.values())
    return min(number for number, count in counts.items() if count == best)


def place_of(postcode: str, municipality: bytes) -> Place:
    """Return the place from the municipality register's answer."""
    row = json.loads(municipality)
    return Place(
        postcode=postcode,
        municipality=str(row["kommunenummer"]),
        municipality_name=str(row.get("kommunenavnNorsk") or row["kommunenavn"]),
        county=str(row["fylkesnummer"]),
        county_name=str(row["fylkesnavn"]),
    )
