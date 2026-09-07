"""NVE's household tariffs per company and county: Norway's tax zones (D13 §5.3, §9).

`NettleiePerOmradePrManedHusholdningFritidEffekttariffer` lists, for every
concessionaire and each county it serves, whether its customers there pay VAT
(`harMva`) and forbruksavgift (`harForbruksavgift`). That settles a company's
zones exactly (F5: 69 of 71 serve one zone). NVE is the directory and the zone
source, never the tariff source: its steps stop at the example customers (§5.4).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["URL", "counties", "zone_key"]

URL: Final = (
    "https://nettleietariffer.dataplattform.nve.no/v1/"
    "NettleiePerOmradePrManedHusholdningFritidEffekttariffer"
    "?FraDato={day}&Tariffgruppe=Husholdning&Kundegruppe=1"
)


def zone_key(has_vat: bool, has_levy: bool) -> str:
    """Return the NO module's zone for NVE's two flags: `""`, `nord` or `tiltakssone`."""
    if has_vat:
        return ""
    return "nord" if has_levy else "tiltakssone"


def counties(document: bytes) -> Mapping[str, tuple[tuple[str, str], ...]]:
    """Return each organisation number's counties and their zones, sorted by county."""
    found: dict[str, dict[str, str]] = {}
    for row in json.loads(document):
        zones = found.setdefault(str(row["organisasjonsnr"]), {})
        zones[str(row["fylke"]).strip()] = zone_key(
            bool(row["harMva"]), bool(row["harForbruksavgift"])
        )
    return {org: tuple(sorted(zones.items())) for org, zones in found.items()}
