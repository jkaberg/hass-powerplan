"""URDB's fetcher: the utilities serving the ZIP code, then one rate (D13 §5.5; T1a).

Two requests a flow - the ZIP code's rates, the chosen rate in full - with
api.data.gov's shared key (50 a day per address). Held for the flow or the
renewal and released with it (§5.2 rule 6). The rules are
`core/tariffs/sources/openei_urdb.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    QualityError,
    Tier,
    openei_urdb,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["OpenEiUrdb"]


@register
class OpenEiUrdb:
    """The US's T1a: every approved residential rate of the utility serving the ZIP code."""

    key: ClassVar[str] = openei_urdb.KEY
    country: ClassVar[str] = "US"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit(
        "OpenEI (NREL)", "https://openei.org/wiki/Utility_Rate_Database", None
    )

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the utilities serving the ZIP code; none without one."""
        if not postcode:
            return []
        return openei_urdb.operators(await http.get(openei_urdb.rates_url(postcode)), http.today())

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return the chosen rate as the copy."""
        if product is None:
            msg = f"{openei_urdb.KEY}: a rate must be chosen for {operator}"
            raise QualityError(msg)
        document = await http.get(openei_urdb.rate_url(product))
        return openei_urdb.parse(document, operator, fetched=http.today(), answers=answers)
