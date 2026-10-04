"""D13 §5.7, D9 §5.15 - the canary compares a company across tiers as the household pays it.

Offline: two copies of Tensio TS, one published incl. VAT and levies and one
excl., agree; a corrected night rate is a finding. The live run is the nightly
workflow's, never the PR suite's.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aiohttp.http_exceptions import HttpProcessingError
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from custom_components.powerplan.core.tariffs.household import (
    EXCL,
    StateTerms,
    TaxZone,
    from_preset,
    published_levies_at,
)
from custom_components.powerplan.core.tariffs.sources import UnreachableError
from tests.builders.presets import fixture_raw
from tools import tariff_canary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

OSLO = ZoneInfo("Europe/Oslo")
DAY = date(2026, 9, 24)


def _tensio():  # type: ignore[no-untyped-def]
    return from_preset(fixture_raw("no/tensio-ts"), source="page", zone=TaxZone("NO")).grid


def _excl(grid):  # type: ignore[no-untyped-def]
    state = StateTerms(zone=TaxZone("NO"))
    energy = tuple(
        replace(
            version,
            periods=tuple(
                replace(
                    period,
                    price=period.price / Decimal("1.25")
                    - published_levies_at(state, version.valid_from, grid.basis),
                )
                for period in version.periods
            ),
        )
        for version in grid.energy
    )
    return replace(grid, basis=EXCL, energy=energy)


def test_two_bases_of_one_tariff_agree_on_energy() -> None:
    """fri-nettleie publishes excl. VAT and levies, the company's page incl.: no finding."""
    found = tariff_canary.disagreements(_tensio(), _excl(_tensio()), "NO", DAY, OSLO)
    assert [line for line in found if line.startswith("energy")] == []


def test_a_changed_night_rate_is_a_finding() -> None:
    """One tier moved the night rate by a whole øre: the maintainer hears of it."""
    grid = _tensio()
    moved = replace(
        grid,
        energy=(
            *grid.energy[:-1],
            replace(
                grid.energy[-1],
                periods=(
                    grid.energy[-1].periods[0],
                    replace(grid.energy[-1].periods[1], price=Decimal("0.2479")),
                ),
            ),
        ),
    )
    found = tariff_canary.disagreements(grid, moved, "NO", DAY, OSLO)
    assert any(line.startswith("energy") for line in found)


def test_the_report_names_each_finding() -> None:
    """A row per finding; nothing found says so."""
    rows = [tariff_canary.Finding("fri_nettleie / tensio_page", "Tensio TS", "energy at …")]
    text = tariff_canary.report(rows, DAY)
    assert "| `fri_nettleie / tensio_page` | Tensio TS | energy at … |" in text
    assert tariff_canary.report([], DAY).startswith("Every tariff source answered")


async def test_fri_nettleies_contract_holds_on_the_captured_archive() -> None:
    """Every company the archive lists gives a copy: no finding, and the downloads let go."""
    from custom_components.powerplan.core.tariffs.sources import fri_nettleie  # noqa: PLC0415
    from custom_components.powerplan.providers.tariffs import base  # noqa: PLC0415
    from custom_components.powerplan.providers.tariffs.fri_nettleie import (  # noqa: PLC0415
        FriNettleie,
    )
    from tests.builders.tariff_sources import fri_http  # noqa: PLC0415

    base.register(FriNettleie)
    http = fri_http()
    findings = await tariff_canary._cross_check(http, ["NO"], DAY, OSLO)  # type: ignore[arg-type]
    assert findings == []
    assert http.asked.count(fri_nettleie.TARBALL) == 1, "one download, one parse, 73 copies"


async def test_denmarks_elpris_and_datahub_agree_on_the_captured_documents() -> None:
    """D13 §5.7: Radius's summer table on elpris.dk is Datahub's, to four decimals."""
    from tests.builders.tariff_sources import denmark_http  # noqa: PLC0415

    findings = await tariff_canary.datahub_check(denmark_http(), DAY)  # type: ignore[arg-type]
    radius = [f for f in findings if f.operator == "Radius Elnet A/S"]
    assert radius == []
    assert findings, "areas 131 and 145 have no Datahub rows captured: said so, not skipped"


def test_a_long_only_source_is_sampled_the_same_every_night() -> None:
    """ElCom's ~2 100 municipalities fit no night: a fixed spread of them does (D-0681)."""
    municipalities = list(range(2135))
    picked = tariff_canary.sample(municipalities)
    assert len(picked) <= tariff_canary.SAMPLE
    assert picked[0] == 0
    assert picked[-1] > 2000, "spread over the list, not its head"
    assert picked == tariff_canary.sample(municipalities)
    companies = list(range(74))
    assert tariff_canary.sample(companies) == companies, "fri-nettleie: every company"


class _Replies:
    """A session whose GETs answer with `statuses` in turn, then 200 and `body`."""

    def __init__(self, statuses: list[int], body: bytes) -> None:
        self.statuses = statuses
        self.body = body
        self.asked = 0

    @asynccontextmanager
    async def get(self, _url: str, **_kwargs: object) -> AsyncIterator[object]:
        self.asked += 1
        status = self.statuses.pop(0) if self.statuses else 200
        body = self.body

        class _Content:
            async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
                yield body

        yield SimpleNamespace(status=status, content=_Content())


async def test_a_429_is_asked_again_and_a_404_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """Energi Data Service's 429s were a quarter of a night's findings: wait and ask again."""
    monkeypatch.setattr(tariff_canary, "BACKOFF_S", (0.0, 0.0, 0.0))
    busy = _Replies([429, 429], b"{}")
    http = tariff_canary.CanaryHttp(busy, DAY)  # type: ignore[arg-type]
    assert await http.get("https://example.invalid/busy") == b"{}"
    assert busy.asked == 3
    gone = _Replies([404], b"{}")
    with pytest.raises(UnreachableError, match="HTTP 404"):
        await tariff_canary.CanaryHttp(gone, DAY).get("https://example.invalid/gone")  # type: ignore[arg-type]
    assert gone.asked == 1


def test_like_products_are_compared_across_tiers() -> None:
    """Eltariff and Ei name a product differently: the dwelling, the fuse and the region pair them."""
    kind = tariff_canary.kind
    assert kind("Apartment 16A - Stockholm") == kind("Lägenhet 16 A – Stockholm")
    assert kind("Fuse 16A - Stockholm") != kind("Villa 16 A – Syd & Mellersta"), "another region"
    assert kind("Konsumtion 16 A") == kind("Villa 16 A")
    assert kind("Prislista Lägenhet 16-25A säkringsabonnemang") == kind("Lägenhet 16 A")
    assert kind("Konsumtion LGH")[1] is None, "no fuse, nothing to pair it with"


async def test_a_retailer_with_no_plan_is_no_finding_unless_every_one_is() -> None:
    """ASENO lists nothing (the flow says so); a source whose every brand lists nothing is one."""
    from custom_components.powerplan.core.tariffs.sources import (  # noqa: PLC0415
        Operator,
        Product,
        Tier,
    )
    from tests.builders.tariff_sources import FixtureHttp, fake  # noqa: PLC0415

    cls = fake("noplans", Tier.T1A, operators=(Operator("aseno", "ASENO"),))

    async def products(self: object, http: object, operator: str, postcode: str | None) -> tuple:
        return () if operator == "aseno" else (Product("plan", "Plan"),)

    cls.products = products  # type: ignore[attr-defined]
    http = FixtureHttp({})
    only = [Operator("aseno", "ASENO")]
    assert await tariff_canary._contract(cls, http, only, country="NO", day=DAY, zone=OSLO) == [  # type: ignore[arg-type]
        tariff_canary.Finding("noplans", "–", "no operator lists a plan")
    ]
    both = [*only, Operator("agl", "AGL")]
    assert await tariff_canary._contract(cls, http, both, country="NO", day=DAY, zone=OSLO) == []  # type: ignore[arg-type]


class _Dropped(_Replies):
    """A session whose first `drops` GETs lose the connection."""

    def __init__(self, drops: int, body: bytes) -> None:
        super().__init__([], body)
        self.drops = drops

    @asynccontextmanager
    async def get(self, url: str, **kwargs: object) -> AsyncIterator[object]:
        if self.drops:
            self.drops -= 1
            self.asked += 1
            raise aiohttp.ServerDisconnectedError
        async with super().get(url, **kwargs) as answer:
            yield answer


async def test_20_a_dropped_connection_is_asked_again_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ei's workbook failed at 03:59 on a dropped connection and fetched at 10:00 (D-0705)."""
    monkeypatch.setattr(tariff_canary, "RETRY_S", (0.0, 0.0))
    once = _Dropped(1, b"{}")
    assert await tariff_canary.CanaryHttp(once, DAY).get("https://example.invalid/ei") == b"{}"  # type: ignore[arg-type]
    assert once.asked == 2
    thrice = _Dropped(3, b"{}")
    with pytest.raises(UnreachableError):
        await tariff_canary.CanaryHttp(thrice, DAY).get("https://example.invalid/ei")  # type: ignore[arg-type]
    assert thrice.asked == 3, "the first try and two more"
    busy = _Replies([503], b"{}")
    assert await tariff_canary.CanaryHttp(busy, DAY).get("https://example.invalid/elcom") == b"{}"  # type: ignore[arg-type]
    assert busy.asked == 2, "a 503 is a server down for a moment"
    answered = _Replies([500], b"{}")
    with pytest.raises(UnreachableError, match="HTTP 500"):
        await tariff_canary.CanaryHttp(answered, DAY).get("https://example.invalid/down")  # type: ignore[arg-type]
    assert answered.asked == 1, "an HTTP answer is not a dropped connection"


class _Failing(_Replies):
    """A session whose every GET raises `err`."""

    def __init__(self, err: BaseException) -> None:
        super().__init__([], b"")
        self.err = err

    @asynccontextmanager
    async def get(self, _url: str, **_kwargs: object) -> AsyncIterator[object]:
        self.asked += 1
        raise self.err
        yield  # a generator, for the context manager


async def test_23_an_answer_aiohttp_cant_read_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ei's workbook was "0, message=''" on four nights: a redirect loop, or what broke the read (D-0707)."""
    monkeypatch.setattr(tariff_canary, "RETRY_S", (0.0, 0.0))
    url = "https://example.invalid/ei.xlsx"
    info = aiohttp.RequestInfo(URL(url), "GET", CIMultiDictProxy(CIMultiDict()), URL(url))
    hops = (SimpleNamespace(headers={"Location": "/login"}),) * 10
    garbled = aiohttp.ClientResponseError(info, ())
    garbled.__cause__ = HttpProcessingError()
    garbled.__cause__.__cause__ = AssertionError()
    for err, said in (
        (aiohttp.TooManyRedirects(info, hops), "redirected 10 times, last to /login"),  # type: ignore[arg-type]
        (garbled, "an answer that doesn't read: AssertionError()"),
        (TimeoutError(), "no answer in 20 s"),
    ):
        session = _Failing(err)
        with pytest.raises(UnreachableError) as raised:
            await tariff_canary.CanaryHttp(session, DAY).get(url)  # type: ignore[arg-type]
        assert str(raised.value) == f"{url}: {said}"
        assert session.asked == 3, "not an HTTP answer: asked again twice"


def test_20_an_acknowledged_finding_is_left_out_until_its_figure_changes(tmp_path: Path) -> None:
    """Dates aside, figures kept; 180 days, then shown again with its date (D-0705)."""
    known = tmp_path / "known.json"
    known.write_text(
        json.dumps(
            [
                {
                    "source": "a / b",
                    "operator": "E.ON",
                    "what": "apartment: energy at …: 1.25 against 1.12",
                    "reason": "r",
                    "since": "2026-09-27",
                }
            ]
        ),
        "utf-8",
    )
    same = tariff_canary.Finding(
        "a / b", "E.ON", "apartment: energy at 2026-10-05T00:00:00+00:00: 1.25 against 1.12"
    )
    moved = tariff_canary.Finding(
        "a / b", "E.ON", "apartment: energy at 2026-10-05T00:00:00+00:00: 1.30 against 1.12"
    )
    other = tariff_canary.Finding(
        "a / b", "Vattenfall", "apartment: energy at 2026-10-05T00:00:00+00:00: 1.25 against 1.12"
    )
    assert tariff_canary.acknowledged([same, moved, other], date(2026, 10, 5), known) == [
        moved,
        other,
    ]
    [again] = tariff_canary.acknowledged([same], date(2027, 4, 1), known)
    assert "acknowledged 2026-09-27" in again.what


def test_20_the_shipped_acknowledgements_are_well_formed() -> None:
    """Each entry has a source, an operator, a finding, a reason and a date."""
    rows = json.loads(tariff_canary.KNOWN.read_text("utf-8"))
    assert rows
    for row in rows:
        assert set(row) == {"source", "operator", "what", "reason", "since"}
        date.fromisoformat(row["since"])
        assert row["reason"]


# --------------------------------------------------------------------------- #
# D13 §19 21 - the flow's path, live (D-0706)
# --------------------------------------------------------------------------- #


def _one_rate(price: str, *free: tuple[int, int]):  # type: ignore[no-untyped-def]
    """Tensio's copy with one energy rate all day, and 0 in the `free` hours (minutes)."""
    from custom_components.powerplan.core.tariffs.household import (  # noqa: PLC0415
        EnergyPeriod,
        EnergyVersion,
    )
    from custom_components.powerplan.core.tariffs.model import TimeFilter  # noqa: PLC0415

    grid = _tensio()
    periods = tuple(
        EnergyPeriod(when=TimeFilter(hours=(span,)), price=Decimal(0), name="free") for span in free
    )
    version = EnergyVersion(valid_from=date(2026, 1, 1), periods=periods, fallback=Decimal(price))
    return replace(grid, energy=(version,))


def test_21_a_flat_tariff_read_as_its_first_hour_is_a_finding() -> None:
    """Denmark's area 357 the old way: 0,0375 at 00 and 0 in the other 23 (D-0703)."""
    grid = _one_rate("0", (0, 60))
    first_only = replace(
        grid,
        energy=(
            replace(
                grid.energy[0],
                periods=(replace(grid.energy[0].periods[0], price=Decimal("0.0375")),),
            ),
        ),
    )
    found = tariff_canary.priced(first_only, "NO", DAY, OSLO)
    assert any("priced in 1 hour(s) of 24" in what for what in found)


def test_21_free_hours_at_midday_are_no_finding() -> None:
    """An Australian-style plan free from 11 to 14 prices 21 hours: not the hour-0 shape."""
    assert tariff_canary.priced(_one_rate("0.30", (660, 840)), "NO", DAY, OSLO) == []


def test_21_a_price_out_of_bounds_is_a_finding() -> None:
    """Above the currency's bound is read as broken; an ordinary price isn't."""
    assert tariff_canary.priced(_one_rate("0.30"), "NO", DAY, OSLO) == []
    [found] = tariff_canary.priced(_one_rate("40"), "NO", DAY, OSLO)[:1]
    assert "NOK/kWh" in found
    assert tariff_canary.BOUNDS["EUR"] == Decimal(3)


async def test_21_a_copy_that_stores_badly_or_refuses_its_defaults_is_a_finding() -> None:
    """The flow's step 1c and `_apply_fetched` on a fetched copy (D-0706)."""
    from custom_components.powerplan.core.tariffs.sources import (  # noqa: PLC0415
        Fetched,
        QualityError,
        Question,
    )
    from tests.builders.tariff_sources import FixtureHttp  # noqa: PLC0415

    class Asks:
        key = "asks"

        async def fetch(
            self, http: object, operator: str, product: object, answers: dict
        ) -> Fetched:
            if answers:
                raise QualityError("asks: the default is refused")
            return Fetched(_tensio(), questions=(Question("measurement", "year", "why"),))

    http = FixtureHttp({})
    kw = {"country": "NO", "day": DAY, "zone": OSLO}
    refused = await tariff_canary.flow_path(
        Asks, http, "x", None, await Asks().fetch(http, "x", None, {}), **kw
    )  # type: ignore[arg-type]
    assert refused == ["refuses its own questions' defaults: asks: the default is refused"]
    broken = Fetched(replace(_tensio(), capacity=()))
    [stores] = await tariff_canary.flow_path(Asks, http, "x", None, broken, **kw)  # type: ignore[arg-type]
    assert stores.startswith("stores but ")
    assert await tariff_canary.flow_path(Asks, http, "x", None, Fetched(_tensio()), **kw) == []  # type: ignore[arg-type]


def test_21_three_products_spread_over_a_retailers_list() -> None:
    """200 plans: the first, the 101st and the last; three or fewer: all of them."""
    plans = [f"p{i}" for i in range(200)]
    assert tariff_canary.spread(plans, tariff_canary.PRODUCTS) == ["p0", "p100", "p199"]
    assert tariff_canary.spread(["a", "b"], 3) == ["a", "b"]
