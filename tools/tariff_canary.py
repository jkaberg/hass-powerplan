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
import json
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
    datahub_pricelist,
    elpris_dk,
)
from custom_components.powerplan.providers.tariffs import base  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from custom_components.powerplan.core.tariffs.sources import Operator


#: A country's only source listing more than `LIMIT` operators is fetched for `SAMPLE`
#: of them, evenly spread: nothing to compare them with, and a spread shows the
#: endpoint's shape. ElCom lists some 2 100 municipalities, at 1-10 s each.
LIMIT = 500
SAMPLE = 25
#: Seconds to wait before asking again after a 429: Energi Data Service sends a
#: few when the canary asks for every Danish area in a row.
BACKOFF_S = (5.0, 20.0, 60.0)


class CanaryHttp(base.Http):
    """`Http`'s conduct outside Home Assistant: its own session, date and threads.

    The requests, the User-Agent, the size cap and the in-memory cache are
    `Http`'s own; what it downloads is released at the run's end (§5.2 rule 6).
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

    async def _download(self, url: str, headers: Mapping[str, str]) -> bytes:
        """Fetch `url`, asking again after a 429 (Too Many Requests)."""
        for pause in (*BACKOFF_S, None):
            try:
                return await super()._download(url, headers)
            except UnreachableError as err:
                if pause is None or not str(err).endswith("HTTP 429"):
                    raise
            await asyncio.sleep(pause)
        raise AssertionError  # unreachable: the last pass returns or raises


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


async def datahub_check(http: CanaryHttp, day: date) -> list[Finding]:
    """Compare each Danish area's tariff in force on elpris.dk with Datahub's row (§5.7)."""
    findings: list[Finding] = []
    try:
        static = json.loads(await http.get(elpris_dk.STATIC))
    except (SourceError, ValueError) as err:
        return [Finding(elpris_dk.KEY, "–", f"no operators: {err}")]
    for operator in elpris_dk.operators(static):
        try:
            area_doc = json.loads(await http.get(elpris_dk.AREA.format(area=operator.key)))
        except (SourceError, ValueError) as err:
            findings.append(Finding(elpris_dk.KEY, operator.name, str(err)))
            continue
        code, owner = elpris_dk.charge_code(area_doc), elpris_dk.owner(static, operator.key)
        tariffs = [
            c for c in area_doc.get("distributionAreaCharges") or () if c.get("chargeId") == code
        ]
        if not code or not owner or not tariffs:
            continue
        since = date.fromisoformat(str(tariffs[-1]["validFrom"]))
        try:
            answer = json.loads(await http.get(datahub_pricelist.query(owner, code, since)))
        except (SourceError, ValueError) as err:
            findings.append(Finding("datahub_pricelist", operator.name, str(err)))
            continue
        theirs = {
            v.valid_from: v
            for v, _ in datahub_pricelist.versions(answer.get("records") or (), code)
        }
        hours = {
            int(h["hoursFrom"]): Decimal(str(h["amount"]))
            for h in tariffs[-1]["distributionAreaChargeHours"]
        }
        if sorted(hours) != list(range(datahub_pricelist.HOURS)):
            # the adapter's own fetch reports it: "does not price every hour"
            continue
        ours = datahub_pricelist.hourly(since, [hours[h] for h in range(datahub_pricelist.HOURS)])
        other = theirs.get(since)
        if other is None:
            findings.append(
                Finding(
                    "elpris_dk / datahub_pricelist",
                    operator.name,
                    f"Datahub has no {code} from {since}",
                )
            )
        elif _rounded(other) != _rounded(ours):
            findings.append(
                Finding(
                    "elpris_dk / datahub_pricelist", operator.name, f"{code} from {since} differs"
                )
            )
    del day
    return findings


def _rounded(version):  # type: ignore[no-untyped-def]
    """Datahub prices to six decimals, elpris.dk to four: compare at four."""
    quantum = Decimal("0.0001")
    return (
        version.fallback.quantize(quantum),
        tuple((p.when, p.price.quantize(quantum)) for p in version.periods),
    )


async def _cross_check(
    http: CanaryHttp, countries_: Sequence[str], day: date, zone: ZoneInfo
) -> list[Finding]:
    findings: list[Finding] = []
    if "DK" in countries_:
        findings.extend(await datahub_check(http, day))
    for country in countries_:
        by_operator: dict[str, list[tuple[str, GridTariff]]] = {}
        sources = base.for_country(country)
        for cls in sources:
            try:
                operators = await cls().operators(http)
            except SourceError as err:
                findings.append(Finding(cls.key, "–", f"no operators: {err}"))
                continue
            if not operators:
                findings.append(Finding(cls.key, "–", "lists no operator"))
            for operator in operators if len(sources) > 1 else sample(operators):
                try:
                    product = await _first_product(cls, http, operator)
                    fetched = await cls().fetch(http, operator.key, product, {})
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


async def _first_product(
    cls: type[base.TariffSource], http: CanaryHttp, operator: Operator
) -> str | None:
    """Return the product the flow pre-selects: the operator's first, asked where it lists none."""
    products = operator.products
    lister = getattr(cls, "products", None)
    if not products and lister is not None:
        products = tuple(await lister(cls(), http, operator.key, None))
    return products[0].key if products else None


def sample[T](items: Sequence[T]) -> Sequence[T]:
    """Return `items`, or `SAMPLE` of them evenly spread past `LIMIT`, the same every night."""
    if len(items) <= LIMIT:
        return items
    step = -(-len(items) // SAMPLE)
    return items[::step]


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
