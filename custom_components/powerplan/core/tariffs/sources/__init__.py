"""Tariff sources: what every adapter shares, and each adapter's pure parser (D13 §5).

A parser turns a source's documents into a `GridTariff` (D13 §3); its fetcher -
HTTP through Home Assistant's shared session and nothing else - lives in
`providers/tariffs/` (INV-2, INV-3). The adapters arrive with their countries
(TS.3–TS.7); this package holds their common ground and the directories.
"""

from . import fri_nettleie, kartverket, nve
from .base import (
    Credit,
    Fetched,
    Merged,
    Operator,
    Product,
    QualityError,
    Question,
    SourceError,
    Tier,
    UnreachableError,
    merge,
    renew_at,
)

__all__ = [
    "Credit",
    "Fetched",
    "Merged",
    "Operator",
    "Product",
    "QualityError",
    "Question",
    "SourceError",
    "Tier",
    "UnreachableError",
    "fri_nettleie",
    "kartverket",
    "merge",
    "nve",
    "renew_at",
]
