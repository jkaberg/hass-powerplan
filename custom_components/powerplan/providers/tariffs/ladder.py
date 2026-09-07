"""The ladder: the first tier that has the operator and passes the check (D13 §5.1, INV-75).

Tried best first - a country-wide API before a company's, an API before a file, a
file before a document. A source that cannot be reached, or whose answer the
quality check refuses, hands over to the next; a lower tier is never the source
when a higher one answered. When none does, the flow offers the country's rule
template and "enter it myself" (§13).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from custom_components.powerplan.core.tariffs.sources import Fetched, SourceError

from .base import TariffSource, for_country

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .base import Http

_LOGGER = logging.getLogger(__name__)

__all__ = ["NoSourceError", "Resolved", "resolve"]


class NoSourceError(SourceError):
    """No tier answered for this operator (§13: `tariff_source_unreachable`)."""


@dataclass(frozen=True, slots=True)
class Resolved:
    """The tier that answered, and what it gave."""

    source: type[TariffSource]
    fetched: Fetched


async def resolve(
    http: Http,
    country: str,
    operator: str,
    product: str | None = None,
    *,
    answers: Mapping[str, Any] | None = None,
    sources: list[type[TariffSource]] | None = None,
) -> Resolved:
    """Return the operator's tariff from the first tier that answers well (INV-75).

    `operator` is the operator's name as the directory has it, matched against
    each source's own list by its name or its key.
    """
    tried: list[str] = []
    for cls in sources if sources is not None else for_country(country):
        source = cls()
        try:
            listed = await source.operators(http)
            found = next(
                (one for one in listed if operator in (one.key, one.name)),
                None,
            )
            if found is None:
                continue
            fetched = await source.fetch(http, found.key, product, answers or {})
        except SourceError as err:
            _LOGGER.info("tariff source %s (%s) fell through: %s", cls.key, cls.tier, err)
            tried.append(f"{cls.key}: {err}")
            continue
        return Resolved(source=cls, fetched=fetched)
    msg = f"no source answered for {operator!r} in {country}: {'; '.join(tried) or 'none lists it'}"
    raise NoSourceError(msg)
