"""Tariff sources: what every adapter shares, and each adapter's pure parser (D13 §5).

A parser turns a source's documents into a `GridTariff` (D13 §3); its fetcher -
HTTP through Home Assistant's shared session and nothing else - lives in
`providers/tariffs/` (INV-2, INV-3). The adapters arrive with their countries
(TS.3–TS.7); this package holds their common ground and the directories.
"""

from . import (
    datahub_pricelist,
    ei_household,
    elpris_dk,
    eltariff,
    fri_nettleie,
    kartverket,
    nve,
    sahkonhinta,
)
from .base import (
    Credit,
    Fetched,
    Merged,
    Operator,
    Place,
    Product,
    QualityError,
    Question,
    SourceError,
    Tier,
    UnreachableError,
    merge,
    renew_at,
    slug,
)

__all__ = [
    "Credit",
    "Fetched",
    "Merged",
    "Operator",
    "Place",
    "Product",
    "QualityError",
    "Question",
    "SourceError",
    "Tier",
    "UnreachableError",
    "cdr_energy",
    "cwape",
    "datahub_pricelist",
    "ei_household",
    "elpris_dk",
    "eltariff",
    "fri_nettleie",
    "kartverket",
    "merge",
    "nve",
    "openei_urdb",
    "renew_at",
    "sahkonhinta",
    "slug",
    "vreg_xlsx",
]
