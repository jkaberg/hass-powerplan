"""The tariff-source protocol, its registry and the conduct every fetch keeps (D13 §5).

A source is registered once with its key, country, tier and credit; a source whose
licence requires credit cannot be registered without one (§6.1). Every request
goes through `Http`: Home Assistant's shared session, a User-Agent that names the
integration and its repository, a timeout, a size cap - and a 401, a 403 or a
captcha page ends the adapter rather than being worked around (§5.2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol

import aiohttp

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Operator,
    Tier,
    UnreachableError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.tariffs.sources import Fetched

__all__ = [
    "MAX_BYTES",
    "USER_AGENT",
    "Http",
    "TariffSource",
    "for_country",
    "get",
    "keys",
    "register",
]

#: §5.2 rule 2: the integration and its repository, on every request.
USER_AGENT: Final = "PowerPlan (Home Assistant; +https://github.com/jkaberg/hass-powerplan)"
#: D13 §11 (security): no document over 5 MB is read.
MAX_BYTES: Final = 5 * 1024 * 1024
TIMEOUT_S: Final = 20.0


class Http:
    """One flow's or one renewal's requests, cached for its life (§5.2 rule 2)."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Use Home Assistant's shared session."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415

        self._session = async_get_clientsession(hass)
        self._cache: dict[str, bytes] = {}

    async def get(self, url: str, **headers: str) -> bytes:
        """Return `url`'s body; `UnreachableError` on anything but a plain answer."""
        if url in self._cache:
            return self._cache[url]
        try:
            async with self._session.get(
                url,
                headers={"User-Agent": USER_AGENT, **headers},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as answer:
                if answer.status != 200:  # noqa: PLR2004 - HTTP OK
                    msg = f"{url}: HTTP {answer.status}"
                    raise UnreachableError(msg)
                body = await answer.content.read(MAX_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UnreachableError(f"{url}: {err}") from err
        if len(body) > MAX_BYTES:
            msg = f"{url}: larger than {MAX_BYTES} bytes"
            raise UnreachableError(msg)
        self._cache[url] = body
        return body


class TariffSource(Protocol):
    """One adapter (D13 §5.11): a fetcher here, its parser in `core/tariffs/sources/`."""

    key: ClassVar[str]
    country: ClassVar[str]
    tier: ClassVar[Tier]
    credit: ClassVar[Credit | None]
    #: A licence that asks for credit (fri-nettleie: CC BY 4.0), or `None`.
    licence: ClassVar[str | None]

    async def operators(self, http: Http) -> list[Operator]:
        """Return the operators this source serves, for the flow's list."""
        ...

    async def fetch(
        self,
        http: Http,
        operator: str,
        product: str | None,
        answers: Mapping[str, Any],
    ) -> Fetched:
        """Return one operator's tariff as a copy; raise `SourceError` on failure.

        `answers` are the fields the household confirmed (step 1c): where the source
        leaves one out the answer fills it, and the copy is built with it (§10).
        """
        ...


_REGISTRY: dict[str, type[TariffSource]] = {}


def register[S: TariffSource](cls: type[S]) -> type[S]:
    """Register a source; refuse one whose licence asks for a credit it does not give (§6.1)."""
    if cls.licence is not None and (cls.credit is None or cls.credit.licence != cls.licence):
        msg = f"{cls.key}: its licence {cls.licence} requires credit, and it declares none"
        raise ValueError(msg)
    _REGISTRY[cls.key] = cls
    return cls


def get(key: str) -> type[TariffSource]:
    """Return a registered source class."""
    return _REGISTRY[key]


def keys() -> tuple[str, ...]:
    """Return every registered source's key, sorted."""
    return tuple(sorted(_REGISTRY))


def for_country(country: str | None) -> list[type[TariffSource]]:
    """Return a country's sources on its ladder, best tier first (INV-75)."""
    code = (country or "").upper()
    return sorted(
        (cls for cls in _REGISTRY.values() if cls.country == code),
        key=lambda cls: (cls.tier.rank, cls.key),
    )


def unregister(key: str) -> None:
    """Remove a source - tests register fakes and take them away again."""
    _REGISTRY.pop(key, None)
