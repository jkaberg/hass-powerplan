"""Tauron Dystrybucja's fetcher: the calculator page, once (D13 §5.10; T4).

One page (≈ 190 kB) per flow or renewal, released with it (§5.2 rule 6). The
rules are `core/tariffs/sources/tauron.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    tauron,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Tauron"]


@register
class Tauron:
    """Poland's T4: Tauron Dystrybucja's household card (the other four DSOs have none)."""

    key: ClassVar[str] = tauron.KEY
    country: ClassVar[str] = "PL"
    tier: ClassVar[Tier] = Tier.T4
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("TAURON Dystrybucja", tauron.PAGE, None)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return Tauron with its G11, G12 and G12w."""
        return tauron.operators(await http.get(tauron.PAGE))

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return the chosen tariff's copy."""
        page = await http.get(tauron.PAGE)
        return tauron.parse(page, product or "g11", fetched=http.today(), answers=answers)
