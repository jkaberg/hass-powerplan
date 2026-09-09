"""Eltariff's fetcher: the catalogue, then each company's `GET /tariffs` (D13 §5.5; T1a).

Nine Swedish companies behind eight endpoints. An endpoint that
does not answer leaves its companies out of the list; the others still show.
Every document is held for the flow or the renewal and released with it (§5.2
rule 6). The rules are `core/tariffs/sources/eltariff.py`'s.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs import countries
from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    QualityError,
    SourceError,
    Tier,
    eltariff,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Eltariff"]

_LOGGER = logging.getLogger(__name__)


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError as err:
        msg = f"{eltariff.KEY}: not JSON: {err}"
        raise QualityError(msg) from err


def zones(country: str) -> tuple[str, ...]:
    """Every tax zone of the country: the source cannot place a company in one (D-0568)."""
    module = countries.get(country)
    if module is None or not module.zones:
        return ()
    return ("", *(zone.key for zone in module.zones))


@register
class Eltariff:
    """Sweden's T1a: the companies that publish the Eltariff standard."""

    key: ClassVar[str] = eltariff.KEY
    country: ClassVar[str] = "SE"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit(
        "Eltariff", "https://github.com/RI-SE/Eltariff-API", None
    )

    async def _documents(self, http: Http) -> tuple[list[Any], dict[str, Any]]:
        catalogue = await http.document(eltariff.CATALOGUE, _json)
        documents: dict[str, Any] = {}
        for url in sorted({str(entry["apiUrl"]) for entry in catalogue}):
            try:
                documents[url] = await http.document(f"{url.rstrip('/')}/tariffs", _json)
            except SourceError as err:
                _LOGGER.info("eltariff endpoint %s did not answer: %s", url, err)
        return catalogue, documents

    async def operators(self, http: Http) -> list[Operator]:
        """Return each company in the catalogue whose endpoint answered."""
        catalogue, documents = await self._documents(http)
        return eltariff.operators(catalogue, documents, zones(self.country))

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one company's product as the copy."""
        catalogue, documents = await self._documents(http)
        entry = next((e for e in catalogue if str(e["companyOrgNo"]) == operator), None)
        document = None if entry is None else documents.get(str(entry["apiUrl"]))
        if entry is None or document is None or product is None:
            msg = f"{eltariff.KEY}: no tariffs for {operator} {product!r}"
            raise QualityError(msg)
        url = f"{str(entry['apiUrl']).rstrip('/')}/tariffs"
        return eltariff.parse(document, operator, product, fetched=http.today(), url=url)
