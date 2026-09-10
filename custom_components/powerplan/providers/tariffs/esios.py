"""ESIOS's fetcher: the last working day's PVPC file (D13 §5.10; T1a).

One ≈ 10 kB file per flow or renewal, released with it (§5.2 rule 6). The rules
are `core/tariffs/sources/esios.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    esios,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Esios"]


@register
class Esios:
    """Spain's T1a: 2.0TD's national energy tolls and charges, from REE."""

    key: ClassVar[str] = esios.KEY
    country: ClassVar[str] = "ES"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("Red Eléctrica (ESIOS)", esios.PAGE, None)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the one national tariff."""
        return esios.operators()

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return 2.0TD's periods from the last working day's file."""
        document = await http.get(esios.url(esios.working_day(http.today())))
        return esios.parse(document, fetched=http.today(), answers=answers)
