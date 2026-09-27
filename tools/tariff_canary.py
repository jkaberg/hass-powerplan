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
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
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
from custom_components.powerplan.core.tariffs.rules import loader  # noqa: E402
from custom_components.powerplan.core.tariffs.sources import (  # noqa: E402
    SourceError,
    UnreachableError,
    datahub_pricelist,
    elpris_dk,
)
from custom_components.powerplan.providers.tariffs import base  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from custom_components.powerplan.core.tariffs.sources import Fetched, Operator, Product


#: A country's only source listing more than `LIMIT` operators is fetched for `SAMPLE`
#: of them, evenly spread: nothing to compare them with, and a spread shows the
#: endpoint's shape. ElCom lists some 2 100 municipalities, at 1-10 s each.
LIMIT = 500
SAMPLE = 25
#: The postcode a source that lists by postcode alone is asked for: Namur, Phoenix.
PROBES: Final = {"cwape": "5000", "openei_urdb": "85004"}
#: Seconds to wait before asking again after a 429: Energi Data Service sends a
#: few when the canary asks for every Danish area in a row.
BACKOFF_S = (5.0, 20.0, 60.0)
#: Seconds to wait before asking again after a dropped connection or a timeout: Ei's
#: workbook failed at 03:59 and fetched at 10:00 (D-0705). An HTTP answer isn't retried.
RETRY_S = (5.0, 30.0)
#: The findings the maintainer has read and that no fix of ours can settle (D-0705).
KNOWN = REPO_ROOT / "tools" / "tariff_canary_known.json"
#: Days an acknowledgement holds before the finding is shown again.
KNOWN_DAYS = 180
#: The highest a household's grid-and-tax price per kWh may be before it's read as broken, by
#: currency: about 3 € a kWh, ten times Europe's dearest grid and tax part (D-0706). The
#: canary's alone - the integration never refuses a household's real tariff.
BOUNDS: Final[dict[str, Decimal]] = {
    currency: Decimal(value)
    for currency, value in {
        "EUR": "3", "CHF": "3", "GBP": "3", "USD": "3.5", "CAD": "5", "AUD": "5", "NZD": "5",
        "NOK": "35", "SEK": "35", "DKK": "25", "PLN": "15", "RON": "15", "CZK": "80", "HUF": "1200",
    }.items()
}  # fmt: skip
#: A day's grid energy priced in this many hours or fewer, every other at 0, is a flat tariff read as
#: its first hour (D-0703); a plan with free hours prices more (D-0706).
FEW_HOURS = 4
#: Products fetched per operator of a source that lists them on demand: first, middle, last (D-0706).
PRODUCTS = 3
#: The answers a server gives while it's down for a moment: asked again like a dropped connection.
_GATEWAY: Final = frozenset({"HTTP 502", "HTTP 503", "HTTP 504"})
_ANSWERED = re.compile(r"HTTP \d{3}$")
#: A finding's dates: the week compared moves every night, the figures don't.
_DATES = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)?")


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
        """Fetch `url`, asking again after a 429 (Too Many Requests) or a dropped connection."""
        dropped = iter(RETRY_S)
        for pause in (*BACKOFF_S, None):
            while True:
                try:
                    return await super()._download(url, headers)
                except UnreachableError as err:
                    wait = _retry(err, dropped)
                    if wait is None:
                        if pause is None or not str(err).endswith("HTTP 429"):
                            raise
                        break
                await asyncio.sleep(wait)
            await asyncio.sleep(pause)
        raise AssertionError  # unreachable: the last pass returns or raises

    async def _send(self, url: str, body: Mapping[str, Any]) -> bytes:
        """POST, asking again after a dropped connection (ElCom's "Server disconnected")."""
        dropped = iter(RETRY_S)
        while True:
            try:
                return await super()._send(url, body)
            except UnreachableError as err:
                wait = _retry(err, dropped)
                if wait is None:
                    raise
            await asyncio.sleep(wait)


def _retry(err: UnreachableError, pauses: Iterator[float]) -> float | None:
    """Return how long to wait before asking again, twice: a dropped connection, a 502/503/504 (D-0705)."""
    answered = _ANSWERED.search(str(err))
    if answered and answered.group(0) not in _GATEWAY:
        return None
    return next(pauses, None)


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
            c
            for c in area_doc.get("distributionAreaCharges") or ()
            if c.get("chargeId") == code and c.get("billingType") == "flex"
        ]
        if not code or not owner or not tariffs:
            continue
        since = date.fromisoformat(str(tariffs[-1]["validFrom"]))
        try:
            answer = json.loads(await http.get(datahub_pricelist.query(code, since)))
        except (SourceError, ValueError) as err:
            findings.append(Finding("datahub_pricelist", operator.name, str(err)))
            continue
        theirs = {
            v.valid_from: v
            for v, _ in datahub_pricelist.versions(
                datahub_pricelist.owned(answer.get("records") or (), owner, operator.key), code
            )
        }
        hours = {
            int(h["hoursFrom"]): Decimal(str(h["amount"]))
            for h in tariffs[-1]["distributionAreaChargeHours"]
        }
        rows = tariffs[-1]["distributionAreaChargeHours"]
        if len(rows) != len(hours) or sorted(hours) != list(range(datahub_pricelist.HOURS)):
            # not a table to compare: the adapter reads Datahub's instead (D-0682)
            continue
        ours = datahub_pricelist.hourly(since, elpris_dk.day_prices(hours))
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
        sources = base.for_country(country)
        listed: list[tuple[type[base.TariffSource], list[Operator]]] = []
        for cls in sources:
            try:
                operators = await cls().operators(http, PROBES.get(cls.key))
            except SourceError as err:
                findings.append(Finding(cls.key, "–", f"no operators: {err}"))
                continue
            except Exception as err:  # an adapter's crash is the finding
                findings.append(Finding(cls.key, "–", f"crashed listing operators: {err!r}"))
                continue
            if not operators:
                findings.append(Finding(cls.key, "–", "lists no operator"))
            listed.append((cls, operators))
        names = [operator.name for _, operators in listed for operator in operators]
        shared = {name for name in names if names.count(name) > 1}
        for cls, operators in listed:
            findings.extend(
                await _contract(
                    cls,
                    http,
                    [o for o in operators if o.name not in shared],
                    country=country,
                    day=day,
                    zone=zone,
                )
            )
        for name in sorted(shared):
            copies = [(cls, o) for cls, operators in listed for o in operators if o.name == name]
            for (cls_a, op_a), (cls_b, op_b) in pairwise(copies):
                findings.extend(
                    await _compare(
                        http, (cls_a, op_a), (cls_b, op_b), country=country, day=day, zone=zone
                    )
                )
    return findings


async def _contract(
    cls: type[base.TariffSource],
    http: CanaryHttp,
    operators: Sequence[Operator],
    *,
    country: str,
    day: date,
    zone: ZoneInfo,
) -> list[Finding]:
    """Fetch each operator a source alone lists, or a spread of a long list, and run the flow's path (§5.7)."""
    findings: list[Finding] = []
    chosen = sample(operators)
    no_plans = 0
    for operator in chosen:
        try:
            products = await _products_to_try(cls, http, operator)
            if not products and hasattr(cls, "products"):
                # a retailer with no residential plan: the flow says so (D-0683)
                no_plans += 1
                continue
            for product in products or [None]:
                fetched = await cls().fetch(http, operator.key, product, {})
                findings.extend(
                    Finding(cls.key, operator.name, what)
                    for what in await flow_path(
                        cls,
                        http,
                        operator.key,
                        product,
                        fetched,
                        country=country,
                        day=day,
                        zone=zone,
                    )
                )
        except SourceError as err:
            findings.append(Finding(cls.key, operator.name, str(err)))
        except Exception as err:  # an adapter's crash is the finding
            findings.append(Finding(cls.key, operator.name, f"crashed: {err!r}"))
    if chosen and no_plans == len(chosen):
        findings.append(Finding(cls.key, "–", "no operator lists a plan"))
    return findings


async def flow_path(
    cls: type[base.TariffSource],
    http: CanaryHttp,
    operator: str,
    product: str | None,
    fetched: Fetched,
    *,
    country: str,
    day: date,
    zone: ZoneInfo,
) -> list[str]:
    """Return what stops the flow's own path on a fetched copy (D13 §5.7, D-0706).

    The questions answered with their defaults and the copy fetched again from the cache;
    the copy stored and read back as `_apply_fetched` does; a week's two days priced.
    """
    if fetched.questions:
        answers = {question.key: question.default for question in fetched.questions}
        try:
            fetched = await cls().fetch(http, operator, product, answers)
        except SourceError as err:
            return [f"refuses its own questions' defaults: {err}"]
    try:
        price = HouseholdPrice(
            grid=fetched.grid, supplier=SupplierContract(), state=StateTerms(zone=TaxZone(country))
        )
        stored = loader.from_raw(loader.dump(spec(price)), source="flow")
        stored.version_at(day)
        loader.summarize(stored, day)
    except Exception as err:  # every way the stored copy can fail is the finding
        return [f"stores but {err!r}"]
    return priced(fetched.grid, country, day, zone)


def priced(grid: GridTariff, country: str, day: date, zone: ZoneInfo) -> list[str]:
    """Return a week's two days' hours that don't price, price out of bounds, or are the hour-0 shape."""
    found: list[str] = []
    bound = BOUNDS.get(grid.currency)
    monday = day - timedelta(days=day.weekday())
    for moment in (monday, monday + timedelta(days=6)):
        version = grid.energy_at(moment)
        charged = 0
        for hour in range(24):
            when = datetime.combine(moment, time(hour), tzinfo=zone)
            try:
                value = paid(grid, country, when.astimezone(UTC), zone)
            except Exception as err:  # an hour the chain can't price is the finding
                found.append(f"{moment} {hour:02d}:00 doesn't price: {err!r}")
                break
            if value < 0 or (bound is not None and value > bound):
                found.append(f"{moment} {hour:02d}:00 prices {value} {grid.currency}/kWh")
                break
            if version is not None:
                own = next(
                    (
                        p.price
                        for p in version.periods
                        if p.when is None or p.when.matches(when, zone, NoHolidays())
                    ),
                    version.fallback,
                )
                charged += own > 0
        if 0 < charged <= FEW_HOURS:
            found.append(f"grid energy on {moment} priced in {charged} hour(s) of 24")
    return found


async def _compare(
    http: CanaryHttp,
    a: tuple[type[base.TariffSource], Operator],
    b: tuple[type[base.TariffSource], Operator],
    *,
    country: str,
    day: date,
    zone: ZoneInfo,
) -> list[Finding]:
    """Compare one company's like products across two tiers: a finding only if no pair agrees."""
    (cls_a, op_a), (cls_b, op_b) = a, b
    source = f"{cls_a.key} / {cls_b.key}"
    try:
        keyed_a = _by_kind(await _products(cls_a, http, op_a))
        keyed_b = _by_kind(await _products(cls_b, http, op_b))
        common = sorted(set(keyed_a) & set(keyed_b), key=_preferred)
        if not common:
            return []
        kind = common[0]
        copies_a = [(k, await cls_a().fetch(http, op_a.key, k, {})) for k in keyed_a[kind]]
        copies_b = [(k, await cls_b().fetch(http, op_b.key, k, {})) for k in keyed_b[kind]]
        path: list[Finding] = []
        for cls, op, copies in ((cls_a, op_a, copies_a), (cls_b, op_b, copies_b)):
            for k, fetched in copies:
                path.extend(
                    Finding(cls.key, op.name, what)
                    for what in await flow_path(
                        cls, http, op.key, k, fetched, country=country, day=day, zone=zone
                    )
                )
        grids_a = [fetched.grid for _, fetched in copies_a]
        grids_b = [fetched.grid for _, fetched in copies_b]
    except SourceError as err:
        return [Finding(source, op_a.name, str(err))]
    except Exception as err:  # an adapter's crash is the finding
        return [Finding(source, op_a.name, f"crashed: {err!r}")]
    found: list[str] = []
    for grid_a in grids_a:
        for grid_b in grids_b:
            differ = disagreements(grid_a, grid_b, country, day, zone)
            if not differ:
                return path
            found = found or differ
    return path + [Finding(source, op_a.name, f"{_label(kind)}: {what}") for what in found]


async def _products(
    cls: type[base.TariffSource], http: CanaryHttp, operator: Operator
) -> tuple[Product, ...]:
    """Return the operator's products, asked of the source where it lists none."""
    lister = getattr(cls, "products", None)
    if operator.products or lister is None:
        return operator.products
    return tuple(await lister(cls(), http, operator.key, None))


#: A product as two tiers can both name it: dwelling, main fuse in A, region.
Kind = tuple[str, int | None, str]


def kind(name: str) -> Kind:
    """Return what a product is, read from its name: "Lägenhet 16 A – Stockholm"."""
    lowered = name.casefold()
    dwelling = "apartment" if re.search(r"lägenhet|apartment|\blgh\b", lowered) else "house"
    amps = re.search(r"(\d+)(?:-\d+)?\s*a\b", lowered)
    region = re.split(r"\s[-–]\s", name)
    return dwelling, int(amps.group(1)) if amps else None, region[-1] if len(region) > 1 else ""


def _by_kind(products: Sequence[Product]) -> dict[Kind, list[str]]:
    found: dict[Kind, list[str]] = {}
    for product in products:
        what = kind(product.name)
        if what[1] is not None:
            found.setdefault(what, []).append(product.key)
    return found


def _preferred(what: Kind) -> tuple[bool, bool, Kind]:
    """Sort a 16 A apartment first: the one product nearly every company lists in both."""
    return (what[0] != "apartment", what[1] != 16, what)  # noqa: PLR2004 - 16 A


def _label(what: Kind) -> str:
    dwelling, amps, region = what
    return f"{dwelling} {amps} A" + (f" ({region})" if region else "")


async def _products_to_try(
    cls: type[base.TariffSource], http: CanaryHttp, operator: Operator
) -> list[str]:
    """Return the products to fetch: the pre-selected one, or the first, middle and last of a list asked on demand."""
    if operator.products:
        return [operator.products[0].key]
    lister = getattr(cls, "products", None)
    if lister is None:
        return []
    listed = [product.key for product in await lister(cls(), http, operator.key, None)]
    return spread(listed, PRODUCTS)


def spread[T](items: Sequence[T], n: int) -> list[T]:
    """Return `n` of `items` spread from the first to the last, each once (D-0706)."""
    if len(items) <= n:
        return list(items)
    picks = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in picks]


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


def _undated(what: str) -> str:
    return _DATES.sub("…", what)


def acknowledged(findings: Sequence[Finding], day: date, path: Path = KNOWN) -> list[Finding]:
    """Leave out the findings the maintainer has acknowledged, dates aside, for `KNOWN_DAYS` (D-0705).

    An entry older than that shows its finding again, dated, so it is read again.
    """
    known = json.loads(path.read_text("utf-8")) if path.exists() else []
    since = {
        (row["source"], row["operator"], _undated(row["what"])): date.fromisoformat(row["since"])
        for row in known
    }
    kept: list[Finding] = []
    for finding in findings:
        when = since.get((finding.source, finding.operator, _undated(finding.what)))
        if when is None:
            kept.append(finding)
        elif (day - when).days > KNOWN_DAYS:
            kept.append(
                Finding(
                    finding.source,
                    finding.operator,
                    f"{finding.what} (acknowledged {when}: read again)",
                )
            )
    return kept


def main(argv: Sequence[str] | None = None) -> int:
    """Print the report; exit 1 on any finding, so the workflow opens an issue."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--country", action="append", default=None)
    parser.add_argument("--at", type=date.fromisoformat, default=date.today())
    parser.add_argument("--time-zone", default="UTC", help="the zone a day's hours are read in")
    args = parser.parse_args(argv)
    chosen = args.country or [code for code in countries.codes() if base.for_country(code)]
    findings = acknowledged(asyncio.run(run(chosen, args.at, args.time_zone)), args.at)
    print(report(findings, args.at))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
