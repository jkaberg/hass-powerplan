"""ElCom's fetcher: the site's own GraphQL, one query per question (D13 §5.10; T1a).

A postcode finds its municipality with the site's search; without one, every
municipality ElCom knows is listed. This year's and next year's components are
read once per flow or renewal and released with it (§5.2 rule 6). The rules are
`core/tariffs/sources/elcom.py`'s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    Tier,
    elcom,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Elcom"]


@register
class Elcom:
    """Switzerland's T1a: every municipality's grid operator and tariff, from ElCom."""

    key: ClassVar[str] = elcom.KEY
    country: ClassVar[str] = "CH"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("ElCom", elcom.PAGE, None)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the postcode's municipality, or every municipality."""
        if postcode:
            found = elcom.municipalities(
                await http.post(elcom.API, elcom.search_query(postcode)), search=True
            )
            if found:
                # The site's search ranks the postcode's own municipality first.
                return elcom.operators(found[:1])
        return elcom.operators(
            elcom.municipalities(await http.post(elcom.API, elcom.municipalities_query()))
        )

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return the municipality's grid tariff for the category, this year and next."""
        category = product or elcom.CATEGORIES[0].key
        year = http.today().year
        documents = {
            when: await http.post(elcom.API, elcom.observations_query(operator, category, when))
            for when in (year, year + 1)
        }
        return elcom.parse(documents, operator, category, fetched=http.today(), answers=answers)
