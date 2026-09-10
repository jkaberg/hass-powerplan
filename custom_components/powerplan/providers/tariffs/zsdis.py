"""ZSDIS's fetcher: the switching-times page, once (D13 §5.10; T4).

One page (≈ 135 kB) per flow or renewal, released with it (§5.2 rule 6). The
rules are `core/tariffs/sources/zsdis.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    zsdis,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Zsdis"]


@register
class Zsdis:
    """Slovakia's T4: western Slovakia's HDO programmes and their windows."""

    key: ClassVar[str] = zsdis.KEY
    country: ClassVar[str] = "SK"
    tier: ClassVar[Tier] = Tier.T4
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("Západoslovenská distribučná", zsdis.PAGE, None)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return ZSDIS with its household programmes."""
        return zsdis.operators(await http.get(zsdis.PAGE))

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return the meter's code's two rates and every code's windows."""
        page = await http.get(zsdis.PAGE)
        if product is None:
            product = zsdis.operators(page)[0].products[0].key
        return zsdis.parse(page, product, fetched=http.today(), answers=answers)
