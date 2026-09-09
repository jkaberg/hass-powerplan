"""Ei's household file: the page's link, then the workbook (D13 §5.5; T6, D-0569).

The workbook (≈ 2.3 MB) is read in the executor, once per flow or renewal, and
released with it (§5.2 rule 6). The rules are `core/tariffs/sources/ei_household.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    ei_household,
)

from .base import register
from .eltariff import zones

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["EiHousehold"]


@register
class EiHousehold:
    """Sweden's T6: every company's household figures, no power fee."""

    key: ClassVar[str] = ei_household.KEY
    country: ClassVar[str] = "SE"
    tier: ClassVar[Tier] = Tier.T6
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("Energimarknadsinspektionen", ei_household.PAGE, None)

    async def _rows(self, http: Http) -> tuple[str, list[ei_household.Row]]:
        url = ei_household.workbook_url(await http.get(ei_household.PAGE))
        return url, await http.document(url, ei_household.read)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return every company with figures for this year."""
        _, rows = await self._rows(http)
        return ei_household.operators(rows, http.today().year, zones(self.country))

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one company's customer group as the copy."""
        url, rows = await self._rows(http)
        return ei_household.parse(
            rows, operator, product or "", fetched=http.today(), url=url, answers=answers
        )
