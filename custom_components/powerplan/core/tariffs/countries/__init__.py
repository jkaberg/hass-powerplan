"""One module per country: VAT, levies, tax zones (D13 §5.1, §9, §9.1).

Importing this package registers every country it ships - the EU's 27, Norway,
Switzerland, the UK, Iceland, the US and Australia. A country is one module;
`get(code)` answers `None` for one without, whose VAT the flow asks (§9.1).
Each declares the time zones that pre-select it, its postcode directory and its schemes; its sources register themselves in `providers/tariffs/` with their country.
"""

from . import (  # noqa: F401
    at,
    au,
    be,
    bg,
    ch,
    cy,
    cz,
    de,
    dk,
    ee,
    es,
    fi,
    fr,
    gb,
    gr,
    hr,
    hu,
    ie,
    is_,
    it,
    lt,
    lu,
    lv,
    mt,
    nl,
    no,
    pl,
    pt,
    ro,
    se,
    si,
    sk,
    us,
)  # fmt: skip
from .base import CountryModule, Levy, Rate, Scheme, Zone, pick
from .registry import codes, for_time_zone, get, register

__all__ = [
    "CountryModule",
    "Levy",
    "Rate",
    "Scheme",
    "Zone",
    "codes",
    "for_time_zone",
    "get",
    "pick",
    "register",
]
