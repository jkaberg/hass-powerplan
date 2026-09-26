"""The CWaPE/Brugel platform's fetcher (D13 §5.9; T1a, D-0575).

The postcode's entries, the grid companies' names and one simulation per entry
for the list; one simulation for the copy. The platform asks for a 10-second
crawl delay; a flow sends a handful of requests, a renewal two. Held for the flow
or the renewal and released with it (§5.2 rule 6).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Final

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Fetched,
    Operator,
    QualityError,
    SourceError,
    Tier,
    cwape,
)

from .base import register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

__all__ = ["Cwape"]

_LOGGER = logging.getLogger(__name__)
#: The platforms answer JSON-LD collections unless asked for JSON (D-0683).
_JSON: Final = {"Accept": "application/json"}


@register
class Cwape:
    """Wallonia's and Brussels' T1a: the regulators' comparators, by postcode."""

    key: ClassVar[str] = cwape.KEY
    country: ClassVar[str] = "BE"
    tier: ClassVar[Tier] = Tier.T1A
    licence: ClassVar[str | None] = None
    credit: ClassVar[Credit | None] = Credit("CWaPE and Brugel", "https://www.compacwape.be", None)

    async def _segment(
        self, http: Http, key: str, host: str, kw: float | None
    ) -> tuple[str | None, bool]:
        """BruSim prices the connection's power; CompaCWaPE's household tariffs do not."""
        if key != "brusim":
            return None, False
        return cwape.segment_for(await http.get(f"{host}/connection_power_segments", **_JSON), kw)

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the grid companies the regulators' platforms name for the postcode."""
        if not postcode:
            return []
        found: list[Operator] = []
        for key, host, _ in cwape.HOSTS:
            try:
                entries = cwape.postal_codes(
                    await http.get(f"{host}/postal_codes?code={postcode}", **_JSON)
                )
                if not entries:
                    continue
                names = cwape.dnm_names(
                    await http.get(f"{host}/distribution_network_managers", **_JSON)
                )
                segment, _ = await self._segment(http, key, host, None)
                named = []
                for postal_id, municipality in entries:
                    answer = await http.post(
                        f"{host}/offer_simulations", cwape.body(postal_id, "single", segment)
                    )
                    company = names.get(cwape.dnm_of(answer), "")
                    named.append((postal_id, municipality, company))
                found.extend(cwape.operators(key, named))
            except SourceError as err:
                _LOGGER.info("%s did not answer for %s: %s", key, postcode, err)
        return found

    async def fetch(
        self, http: Http, operator: str, product: str | None, answers: Mapping[str, Any]
    ) -> Fetched:
        """Return one entry's grid lines as the copy, for the chosen meter."""
        key, _, postal_id = operator.partition(":")
        host = next((h for k, h, _ in cwape.HOSTS if k == key), None)
        if host is None or not postal_id:
            msg = f"{cwape.KEY}: no platform for {operator!r}"
            raise QualityError(msg)
        kw = answers.get("connection_kw")
        segment, assumed = await self._segment(http, key, host, None if kw is None else float(kw))
        answer = await http.post(
            f"{host}/offer_simulations", cwape.body(postal_id, product or "single", segment)
        )
        names = cwape.dnm_names(await http.get(f"{host}/distribution_network_managers", **_JSON))
        return cwape.parse(
            answer,
            operator=operator,
            product=product or "single",
            company=names.get(cwape.dnm_of(answer), ""),
            fetched=http.today(),
            url=f"{host}/offer_simulations",
            answers=answers,
            segment_assumed=assumed,
        )
