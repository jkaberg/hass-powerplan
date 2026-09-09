"""Finland's postcode directory: sahkonhinta.fi, the regulator's price site (D13 §5.9; T1a).

Energiavirasto's sahkonhinta.fi loads `getdsocollection`: its 117 grid companies,
each with the postcodes it serves (some with none listed). Finland has no grid
tariff source (§5.5), so the directory names the company for the household's own
table (`custom`), and nothing more. Pure.
"""

from __future__ import annotations

import json
from typing import Final

from .base import Place, QualityError

__all__ = ["DSOS", "place_of"]

DSOS: Final = "https://ev-shv-prod-app-wa-consumerapi1.azurewebsites.net/api/getdsocollection"


def place_of(postcode: str, document: bytes) -> Place:
    """Return the postcode's grid companies; `QualityError` when the list is not the site's."""
    try:
        companies = json.loads(document)
        names = tuple(
            str(company["Name"]).strip()
            for company in companies
            if postcode in (company.get("PostalCodes") or ())
        )
    except (ValueError, KeyError, TypeError) as err:
        msg = f"sahkonhinta: not the grid company list: {err}"
        raise QualityError(msg) from err
    return Place(postcode=postcode, grid_companies=names)
