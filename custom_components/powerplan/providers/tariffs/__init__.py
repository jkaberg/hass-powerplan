"""Tariff sources' fetchers and the directories (D13 §5): HTTP and nothing else.

Each adapter's parser is pure, in `core/tariffs/sources/`; the fetcher here
downloads the documents through Home Assistant's shared session under §5.2's
conduct and hands them over. Nothing here runs at start (INV-73): the flow, the
monthly renewal and `powerplan.refresh_tariff` are the only callers.
"""

from . import (
    anre,
    cdr_energy,
    cwape,
    ei_household,
    elcom,
    elpris_dk,
    eltariff,
    esios,
    fri_nettleie,
    openei_urdb,
    tauron,
    vreg_xlsx,
    zsdis,
)
from .base import (
    MAX_BYTES,
    USER_AGENT,
    Http,
    TariffSource,
    for_country,
    get,
    keys,
    register,
)
from .ladder import Resolved, resolve

__all__ = [
    "MAX_BYTES",
    "USER_AGENT",
    "Http",
    "Resolved",
    "TariffSource",
    "anre",
    "cdr_energy",
    "cwape",
    "ei_household",
    "elcom",
    "elpris_dk",
    "eltariff",
    "esios",
    "for_country",
    "fri_nettleie",
    "get",
    "keys",
    "openei_urdb",
    "register",
    "resolve",
    "tauron",
    "vreg_xlsx",
    "zsdis",
]
