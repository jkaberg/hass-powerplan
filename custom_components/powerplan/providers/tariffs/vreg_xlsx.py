"""VREG's fetcher: the tariff page's link, then the year's sheet (D13 §5.11; T6).

The sheet (≈ 94 kB) is read in the executor, once per flow or renewal, and
released with it (§5.2 rule 6). The rules are `core/tariffs/sources/vreg_xlsx.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    vreg_xlsx,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["VregXlsx"]


@register
class VregXlsx:
    """Flanders' T6: the eight Fluvius areas' tariffs from the regulator's sheet."""

    key: ClassVar[str] = vreg_xlsx.KEY
    country: ClassVar[str] = "BE"
    tier: ClassVar[Tier] = Tier.T6
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("Vlaamse Nutsregulator", vreg_xlsx.PAGE, None)

    async def _sheet(self, http: Http) -> tuple[str, vreg_xlsx.Sheet]:
        url = vreg_xlsx.workbook_url(await http.get(vreg_xlsx.PAGE), http.today().year)
        return url, await http.document(url, vreg_xlsx.read)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the eight areas."""
        _, sheet = await self._sheet(http)
        return vreg_xlsx.operators(sheet)

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one area's copy for the chosen meter."""
        url, sheet = await self._sheet(http)
        return vreg_xlsx.parse(sheet, operator, product, fetched=http.today(), url=url)
