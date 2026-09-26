"""The tariff-source protocol, its registry and the conduct every fetch keeps (D13 §5).

A source is registered once with its key, country, tier and credit; a source whose
licence requires credit cannot be registered without one (§6.1). Every request
goes through `Http`: Home Assistant's shared session, a User-Agent that names the
integration and its repository, a timeout, a size cap - and a 401, a 403 or a
captcha page ends the adapter rather than being worked around (§5.2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol

import aiohttp

from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Operator,
    Tier,
    UnreachableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date

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
    "read_whole",
    "register",
]

#: §5.2 rule 2: the integration and its repository, on every request.
USER_AGENT: Final = "PowerPlan (Home Assistant; +https://github.com/jkaberg/hass-powerplan)"
#: D13 §11 (security): no document over 5 MB is read.
MAX_BYTES: Final = 5 * 1024 * 1024
TIMEOUT_S: Final = 20.0


async def read_whole(answer: aiohttp.ClientResponse) -> bytes:
    """Read a body to its end, stopping one byte past `MAX_BYTES` (D13 §11).

    `StreamReader.read(n)` returns whatever has arrived, up to `n` - a 1.5 MB
    archive came back cut after its first chunks and failed to unpack. A body that
    ends early (a dropped connection) raises `ClientPayloadError`: unreachable.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in answer.content.iter_chunked(64 * 1024):
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_BYTES:
            break
    return b"".join(chunks)


class Http:
    """One flow's or one renewal's requests, cached in memory for its life (§5.2 rules 2, 6).

    Nothing is written to disk; `release()` drops every response once the copy is taken.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Use Home Assistant's shared session."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415

        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._cache: dict[str, bytes] = {}
        self._parsed: dict[tuple[str, Callable[[bytes], Any]], Any] = {}

    def today(self) -> date:
        """Return the site's date: what a fetch is dated by."""
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        return dt_util.now().date()

    async def executor[T](self, job: Callable[..., T], *args: Any) -> T:
        """Run a parse too heavy for the event loop in Home Assistant's executor."""
        return await self._hass.async_add_executor_job(job, *args)

    def release(self) -> None:
        """Drop every response held: the copy is taken, the downloads are not kept (rule 6)."""
        self._cache.clear()
        self._parsed.clear()

    async def document[T](self, url: str, parse: Callable[[bytes], T]) -> T:
        """Return `url` parsed, in the executor, once for this flow or renewal."""
        key = (url, parse)
        if key not in self._parsed:
            self._parsed[key] = await self.executor(parse, await self.get(url))
        result: T = self._parsed[key]
        return result

    async def post(self, url: str, body: Mapping[str, Any]) -> bytes:
        """Return a JSON POST's answer, sent once for this flow or renewal (§5.2 rule 1).

        Only where the regulator's own page posts the same for any visitor
        (CompaCWaPE's `offer_simulations`).
        """
        key = f"POST {url} {json.dumps(body, sort_keys=True)}"
        if key not in self._cache:
            self._cache[key] = await self._send(url, body)
        return self._cache[key]

    async def _send(self, url: str, body: Mapping[str, Any]) -> bytes:
        try:
            async with self._session.post(
                url,
                json=dict(body),
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as answer:
                if answer.status not in {200, 201}:
                    msg = f"{url}: HTTP {answer.status}"
                    raise UnreachableError(msg)
                data = await read_whole(answer)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UnreachableError(f"{url}: {err}") from err
        if len(data) > MAX_BYTES:
            msg = f"{url}: larger than {MAX_BYTES} bytes"
            raise UnreachableError(msg)
        return data

    async def get(self, url: str, **headers: str) -> bytes:
        """Return `url`'s body, downloaded once for this flow or renewal (rule 2)."""
        if url not in self._cache:
            self._cache[url] = await self._download(url, headers)
        return self._cache[url]

    async def _download(self, url: str, headers: Mapping[str, str]) -> bytes:
        """Fetch `url`; `UnreachableError` on anything but a plain answer."""
        try:
            async with self._session.get(
                url,
                headers={"User-Agent": USER_AGENT, **headers},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as answer:
                if answer.status != 200:  # noqa: PLR2004 - HTTP OK
                    msg = f"{url}: HTTP {answer.status}"
                    raise UnreachableError(msg)
                body = await read_whole(answer)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UnreachableError(f"{url}: {err}") from err
        if len(body) > MAX_BYTES:
            msg = f"{url}: larger than {MAX_BYTES} bytes"
            raise UnreachableError(msg)
        return body


class TariffSource(Protocol):
    """One adapter (D13 §5.11): a fetcher here, its parser in `core/tariffs/sources/`."""

    key: ClassVar[str]
    country: ClassVar[str]
    tier: ClassVar[Tier]
    credit: ClassVar[Credit | None]
    #: A licence that asks for credit (fri-nettleie: CC BY 4.0), or `None`.
    licence: ClassVar[str | None]

    async def operators(self, http: Http, postcode: str | None = None) -> list[Operator]:
        """Return the operators this source serves, for the flow's list.

        `postcode` is the household's, where the country's source keys its list by
        it (URDB, CWaPE: D13 §5.3); a source that does not ignores it.
        """
        ...

    # A source whose products are too many to list with every operator (CDR:
    # a brand's plans) also defines `products(http, operator, postcode)`; the flow
    # asks it once the household has chosen the operator (D13 §6 step 1a).

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
