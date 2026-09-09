"""elpris.dk's fetcher, with Datahub for the next season (D13 §5.9; T1a, D-0571).

Three documents from elpris.dk and, for the area's owner, one Datahub query;
Datahub down leaves the copy with the tariff in force. Every document is held for
the flow or the renewal and released with it (§5.2 rule 6).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    QualityError,
    SourceError,
    Tier,
    datahub_pricelist,
    elpris_dk,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["ElprisDk"]

_LOGGER = logging.getLogger(__name__)


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError as err:
        msg = f"{elpris_dk.KEY}: not JSON: {err}"
        raise QualityError(msg) from err


@register
class ElprisDk:
    """Denmark's T1a: every grid area, from the regulator's own price site."""

    key: ClassVar[str] = elpris_dk.KEY
    country: ClassVar[str] = "DK"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit(
        "elpris.dk (Forsyningstilsynet)", "https://elpris.dk", None
    )

    async def operators(self, http: Http) -> list[Operator]:
        """Return every grid area elpris.dk lists."""
        return elpris_dk.operators(await http.document(elpris_dk.STATIC, _json))

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one area's copy: elpris.dk's tariff and Datahub's next season."""
        static = await http.document(elpris_dk.STATIC, _json)
        area_doc = await http.document(elpris_dk.AREA.format(area=operator), _json)
        national = await http.document(elpris_dk.NATIONAL, _json)
        owner = elpris_dk.owner(static, operator)
        code = elpris_dk.charge_code(area_doc)
        future: list[Any] = []
        if owner and code:
            try:
                answer = await http.document(
                    datahub_pricelist.query(owner, code, http.today()), _json
                )
                future = list(answer.get("records") or ())
            except SourceError as err:
                _LOGGER.info("datahub did not answer for %s: %s", owner, err)
        name = next((o.name for o in elpris_dk.operators(static) if o.key == operator), operator)
        return elpris_dk.parse(
            area_doc, national, future, area=operator, name=name, fetched=http.today()
        )
