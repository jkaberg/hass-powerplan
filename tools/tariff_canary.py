#!/usr/bin/env python3
"""The nightly canary for tariff sources (D13 §5.7, D9 §5.15) - never in a household's HA.

`uv run python tools/tariff_canary.py [--country NO] [--at YYYY-MM-DD]` fetches
every registered source's live endpoint with its named User-Agent: each source
must list operators and give a tariff the tariff model accepts (its contract);
and each company that more than one tier lists is compared across them, as the
household pays it, on one weekday and one Sunday of the day's version. It prints
one markdown row per failure and exits 1 when there is any - the workflow opens
an issue for the maintainer. The PR suite never runs this (D9: no network).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.powerplan.core.model import Confidence, Slot  # noqa: E402
from custom_components.powerplan.core.pricing import party  # noqa: E402
from custom_components.powerplan.core.pricing.context import PriceContext  # noqa: E402
from custom_components.powerplan.core.pricing.holidays import NoHolidays  # noqa: E402
from custom_components.powerplan.core.tariffs import countries  # noqa: E402
from custom_components.powerplan.core.tariffs.household import (  # noqa: E402
    GridTariff,
    HouseholdPrice,
    StateTerms,
    SupplierContract,
    TaxZone,
    spec,
)
from custom_components.powerplan.core.tariffs.sources import (  # noqa: E402
    SourceError,
    UnreachableError,
)
from custom_components.powerplan.providers.tariffs import base  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

TIMEOUT_S = 30.0


class CanaryHttp:
    """`Http`'s conduct outside Home Assistant: the named User-Agent, the size cap, a cache.

    What it downloads is held in memory for the run and released at its end (§5.2 rule 6).
    """

    def __init__(self, session: aiohttp.ClientSession, day: date) -> None:
        """Use one session for the run, dated `day`."""
        self._session = session
        self._day = day
        self._cache: dict[str, bytes] = {}
        self._parsed: dict[tuple[str, Callable[[bytes], Any]], Any] = {}

    def today(self) -> date:
        """Return the run's day: what every fetch is dated by."""
        return self._day

    async def executor[T](self, job: Callable[..., T], *args: Any) -> T:
        """Run a parse off the event loop."""
        return await asyncio.to_thread(job, *args)

    def release(self) -> None:
        """Drop every download."""
        self._cache.clear()
        self._parsed.clear()

    async def document(self, url: str, parse: Callable[[bytes], Any]) -> Any:
        """Return `url` parsed, once for the run."""
        key = (url, parse)
        if key not in self._parsed:
            self._parsed[key] = await self.executor(parse, await self.get(url))
        return self._parsed[key]

    async def get(self, url: str, **headers: str) -> bytes:
        """Return `url`'s body, or raise `UnreachableError`."""
        if url in self._cache:
            return self._cache[url]
        try:
            async with self._session.get(
                url,
                headers={"User-Agent": base.USER_AGENT, **headers},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as answer:
                if answer.status != 200:  # noqa: PLR2004 - HTTP OK
                    raise UnreachableError(f"{url}: HTTP {answer.status}")
                body = await answer.content.read(base.MAX_BYTES + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UnreachableError(f"{url}: {err}") from err
        if len(body) > base.MAX_BYTES:
            raise UnreachableError(f"{url}: larger than {base.MAX_BYTES} bytes")
        self._cache[url] = body
        return body


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the maintainer should look at."""

    source: str
    operator: str
    what: str


def paid(grid: GridTariff, country: str, when: datetime, zone: ZoneInfo) -> Decimal:
    """Return what the grid party costs a household at `when`, with the country's taxes."""
    price = HouseholdPrice(
        grid=grid, supplier=SupplierContract(), state=StateTerms(zone=TaxZone(country))
    )
    chain, _ = party.chain(price, (), frozenset({"spot"}))
    slot = Slot(start=when, end=when, total=Decimal(0), components={}, confidence=Confidence.KNOWN)
    ctx = PriceContext(
        now=when,
        tz=zone,
        currency=grid.currency,
        mtd_kwh_at=lambda _at: 0.0,
        ytd_kwh_at=lambda _at: 0.0,
        day_type_at=lambda _day: None,
        holidays=NoHolidays(),
    )
    for stage in chain:
        slot = stage.apply(slot, ctx)
    return slot.total


def disagreements(
    a: GridTariff, b: GridTariff, country: str, day: date, zone: ZoneInfo
) -> list[str]:
    """Return where two tiers' copies of one company differ, as the household pays it (§5.7)."""
    found: list[str] = []
    fees = [
        spec(
            HouseholdPrice(grid=g, supplier=SupplierContract(), state=StateTerms(TaxZone(country)))
        )
        .version_at(day)
        .rules
        for g in (a, b)
    ]
    if fees[0] != fees[1]:
        found.append(f"capacity on {day} differs")
    monday = day - timedelta(days=day.weekday())
    for moment in (monday, monday + timedelta(days=6)):
        for hour in range(24):
            when = datetime.combine(moment, time(hour), tzinfo=zone).astimezone(UTC)
            left, right = paid(a, country, when, zone), paid(b, country, when, zone)
            if abs(left - right) > Decimal("0.0005"):
                found.append(f"energy at {when.isoformat()}: {left} against {right}")
                break
    return found


async def run(countries_: Sequence[str], day: date, time_zone: str) -> list[Finding]:
    """Fetch every source's live endpoint and cross-check the tiers (§5.7)."""
    findings: list[Finding] = []
    zone = ZoneInfo(time_zone)
    async with aiohttp.ClientSession() as session:
        http = CanaryHttp(session, day)
        try:
            findings.extend(await _cross_check(http, countries_, day, zone))
        finally:
            http.release()
    return findings


async def _cross_check(
    http: CanaryHttp, countries_: Sequence[str], day: date, zone: ZoneInfo
) -> list[Finding]:
    findings: list[Finding] = []
    for country in countries_:
        by_operator: dict[str, list[tuple[str, GridTariff]]] = {}
        for cls in base.for_country(country):
            try:
                operators = await cls().operators(http)  # type: ignore[arg-type]
            except SourceError as err:
                findings.append(Finding(cls.key, "—", f"no operators: {err}"))
                continue
            if not operators:
                findings.append(Finding(cls.key, "—", "lists no operator"))
            for operator in operators:
                try:
                    fetched = await cls().fetch(http, operator.key, None, {})  # type: ignore[arg-type]
                except SourceError as err:
                    findings.append(Finding(cls.key, operator.name, str(err)))
                    continue
                by_operator.setdefault(operator.name, []).append((cls.key, fetched.grid))
        for name, copies in by_operator.items():
            for (key_a, grid_a), (key_b, grid_b) in pairwise(copies):
                findings.extend(
                    Finding(f"{key_a} / {key_b}", name, what)
                    for what in disagreements(grid_a, grid_b, country, day, zone)
                )
    return findings


def report(findings: Sequence[Finding], day: date) -> str:
    """Return the markdown the workflow writes to the summary and the issue."""
    if not findings:
        return f"Every tariff source answered and agreed on {day}."
    lines = [
        f"{len(findings)} tariff source finding(s) on {day}:",
        "",
        "| source | operator | what |",
        "|---|---|---|",
    ]
    lines += [f"| `{row.source}` | {row.operator} | {row.what} |" for row in findings]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the report; exit 1 on any finding, so the workflow opens an issue."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--country", action="append", default=None)
    parser.add_argument("--at", type=date.fromisoformat, default=date.today())
    parser.add_argument("--time-zone", default="UTC", help="the zone a day's hours are read in")
    args = parser.parse_args(argv)
    chosen = args.country or [code for code in countries.codes() if base.for_country(code)]
    findings = asyncio.run(run(chosen, args.at, args.time_zone))
    print(report(findings, args.at))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
