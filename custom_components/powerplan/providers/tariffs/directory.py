"""A postcode to a place, through the country's official directory only (D13 §5.3, O17).

Norway: Kartverket. A country with no directory has none here, and the flow asks
the grid company and the zone instead; so does an unreachable directory (§13).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from custom_components.powerplan.core.tariffs.sources import kartverket

if TYPE_CHECKING:
    from custom_components.powerplan.core.tariffs.countries import CountryModule
    from custom_components.powerplan.core.tariffs.sources.kartverket import Place

    from .base import Http

__all__ = ["place_for", "valid"]

#: What a postcode looks like where a directory takes one.
_FORMS: Final = {"kartverket": re.compile(r"^\d{4}$")}


def valid(module: CountryModule, postcode: str) -> bool:
    """Return whether `postcode` has the country's form (NO: four digits)."""
    form = _FORMS.get(module.postcode or "")
    return form is not None and bool(form.match(postcode))


async def place_for(http: Http, module: CountryModule, postcode: str) -> Place:
    """Return the postcode's place; `SourceError` when the directory cannot say."""
    number = kartverket.municipality_of(
        await http.get(kartverket.ADDRESSES.format(postcode=postcode))
    )
    return kartverket.place_of(
        postcode, await http.get(kartverket.MUNICIPALITY.format(number=number))
    )
