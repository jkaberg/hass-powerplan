"""ANRE's fetcher: the comparator's counties, then one zone's offers (D13 §5.9; T1a).

One call per renewal (≈ 1.2 MB); read in the executor and released with the flow
or renewal (§5.2 rule 6). Where the comparator's state lines differ from the RO
module's, the difference is logged: the module is corrected by a release, never
copied blind (§9.1). The rules are `core/tariffs/sources/anre.py`'s.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs import countries
from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    anre,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Anre"]

_LOGGER = logging.getLogger(__name__)


@register
class Anre:
    """Romania's T1a: every distribution zone's regulated lines, from ANRE's comparator."""

    key: ClassVar[str] = anre.KEY
    country: ClassVar[str] = "RO"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("ANRE", anre.PAGE, None)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the eight distribution zones, named with their counties."""
        return anre.operators(await http.get(anre.COUNTIES))

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one zone's grid lines; say where the state's have moved on from the module."""
        today = http.today()
        url = anre.offers_url(operator, today)
        document = await http.get(url)
        name = next(
            (row.name for row in await self.operators(http) if row.key == operator), operator
        )
        module = countries.get("RO")
        if module is not None:
            stated = await http.executor(anre.levies, document)
            for line in anre.disagreements(stated, module.levies_at(today)):
                _LOGGER.warning("ANRE's comparator and the RO module differ: %s", line)
        return await http.executor(
            lambda: anre.parse(document, operator, name=name, fetched=today, url=url)
        )
