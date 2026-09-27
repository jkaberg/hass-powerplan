"""The CDR's fetcher: the register's brands, a brand's plans, one plan (D13 §5.5; T1a).

The register for the list; a brand's plans only once the household has chosen the
brand (its products on demand, D13 §6 step 1a); the plan in full for the copy.
Held for the flow or the renewal and released with it (§5.2 rule 6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Final

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    NotFoundError,
    Operator,
    Product,
    QualityError,
    Tier,
    cdr_energy,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["CdrEnergy"]

_LIST: Final = {"x-v": "1"}
#: The register's version that carries each brand's `productBaseUri` (D-0701).
_REGISTER: Final = {"x-v": "2"}
#: The most pages of plans read for one brand: 20 000 plans (D-0702).
_PAGES: Final = 20
_DETAIL: Final = {"x-v": "3"}


@register
class CdrEnergy:
    """Australia's T1a: every retailer's plans, from the Consumer Data Right."""

    key: ClassVar[str] = cdr_energy.KEY
    country: ClassVar[str] = "AU"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("Consumer Data Right", "https://www.cdr.gov.au", None)

    async def _brands(self, http: Http) -> tuple[list[Operator], dict[str, str]]:
        return cdr_energy.brands(await http.get(cdr_energy.REGISTER, **_REGISTER))

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return every energy retailer's brand; its plans are asked once it is chosen."""
        found, _ = await self._brands(http)
        return found

    async def products(
        self, http: Http, operator: str, postcode: str | None
    ) -> tuple[Product, ...]:
        """Return the brand's residential electricity plans for the postcode, over every page."""
        found, bases = await self._brands(http)
        base = bases.get(operator)
        if base is None:
            msg = f"{cdr_energy.KEY}: no brand {operator!r}"
            raise QualityError(msg)
        name = next(brand.name for brand in found if brand.key == operator)
        pages: list[bytes] = []
        url: str | None = cdr_energy.plans_url(base)
        try:
            while url is not None and len(pages) < _PAGES:
                pages.append(await http.get(url, **_LIST))
                url = cdr_energy.next_page(pages[-1])
        except NotFoundError:
            # a host with no plan list: a brand that publishes none (D-0702)
            if not pages:
                return ()
            raise
        return cdr_energy.products(pages, postcode, name)

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return the chosen plan as the copy."""
        _, bases = await self._brands(http)
        base = bases.get(operator)
        if base is None or product is None:
            msg = f"{cdr_energy.KEY}: a plan must be chosen for {operator!r}"
            raise QualityError(msg)
        url = cdr_energy.plan_url(base, product)
        return cdr_energy.parse(
            await http.get(url, **_DETAIL), operator, fetched=http.today(), url=url, answers=answers
        )
