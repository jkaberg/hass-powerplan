"""Belgium, the US, Australia and Finland's directory on their captured documents (D13 §5).

No socket opens; every document and every POST answer is the captured one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.powerplan.core.tariffs.sources import Operator, Tier
from custom_components.powerplan.providers.tariffs import base
from custom_components.powerplan.providers.tariffs.cdr_energy import CdrEnergy
from custom_components.powerplan.providers.tariffs.cwape import Cwape
from custom_components.powerplan.providers.tariffs.openei_urdb import OpenEiUrdb
from custom_components.powerplan.providers.tariffs.vreg_xlsx import VregXlsx
from tests.builders.tariff_sources import australia_http, belgium_http, us_http

if TYPE_CHECKING:
    import pytest

GEE = "a983ebe7-14b5-4c15-8d69-6d5aac7f47ef"


def test_belgiums_ladder_is_the_comparators_then_vregs_sheet() -> None:
    """INV-75: the regulators' APIs (Wallonia, Brussels) before Flanders' document."""
    base.register(Cwape)
    base.register(VregXlsx)
    assert base.for_country("BE") == [Cwape, VregXlsx]
    assert (Cwape.tier, VregXlsx.tier) == (Tier.T1A, Tier.T6)


async def test_namur_lists_ores_and_its_dual_rate_copy() -> None:
    """5000 → ORES; the dual-rate answer is the copy, the day's hours asked."""
    http = belgium_http()
    [ores] = await Cwape().operators(http, "5000")  # type: ignore[arg-type]
    assert (ores.key, ores.name) == ("compacwape:117", "ORES")
    fetched = await Cwape().fetch(http, ores.key, "dual", {})  # type: ignore[arg-type]
    assert [q.key for q in fetched.questions] == ["rate_1_hours"]


async def test_brussels_picks_the_segment_from_the_sites_power() -> None:
    """A 9.2 kW connection is segment 3: the captured answer, nothing asked about it."""
    http = belgium_http()
    [company] = await Cwape().operators(http, "1000")  # type: ignore[arg-type]
    fetched = await Cwape().fetch(http, company.key, "single", {"connection_kw": 9.2})  # type: ignore[arg-type]
    assert "connection_kw" not in [q.key for q in fetched.questions]


async def test_flanders_lists_the_eight_areas() -> None:
    """VREG's sheet: eight Fluvius areas, each with the digital and the analogue meter."""
    operators = await VregXlsx().operators(belgium_http())  # type: ignore[arg-type]
    assert len(operators) == 8
    fetched = await VregXlsx().fetch(belgium_http(), "fi", "digital", {})  # type: ignore[arg-type]
    assert fetched.grid.provenance.tier == "T6"


async def test_urdb_lists_nothing_without_a_zip_code() -> None:
    """The US list is keyed by the ZIP code: none given, none listed."""
    assert await OpenEiUrdb().operators(us_http()) == []  # type: ignore[arg-type]
    [aps] = await OpenEiUrdb().operators(us_http(), "85004")  # type: ignore[arg-type]
    fetched = await OpenEiUrdb().fetch(us_http(), aps.key, "69a718961822c9da260daf1b", {})  # type: ignore[arg-type]
    assert fetched.grid.operator == "Arizona Public Service Co"


async def test_a_cdr_brands_plans_come_once_it_is_chosen() -> None:
    """84 brands to choose from; GEE's plans for 4350 after; the plan as the copy."""
    http = australia_http()
    source = CdrEnergy()
    brands = await source.operators(http)  # type: ignore[arg-type]
    assert len(brands) == 84
    plans = await source.products(http, GEE, "4350")  # type: ignore[arg-type]
    assert plans
    fetched = await source.fetch(http, GEE, "GEE1037096MRE1@EME", {})  # type: ignore[arg-type]
    assert {q.key for q in fetched.questions} == {"measurement", "demand_price"}


async def test_the_flow_asks_a_source_for_products_it_did_not_list(
    hass: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D13 §6 step 1a: a brand listed without plans gets them from its source on choice."""
    from custom_components.powerplan.config_flow import PowerplanConfigFlow  # noqa: PLC0415

    captured = australia_http()

    async def download(self: base.Http, url: str, headers: Any) -> bytes:
        return await captured.get(url)

    monkeypatch.setattr(base.Http, "_download", download)
    base.register(CdrEnergy)
    flow = PowerplanConfigFlow()
    flow.hass = hass
    flow._postcode = "4350"
    chosen = await flow._with_products(Operator(GEE, "GEE Energy", source=CdrEnergy.key))
    assert "GEE1037096MRE1@EME" in [product.key for product in chosen.products]


async def test_finlands_postcode_names_its_grid_companies() -> None:
    """FI: sahkonhinta.fi's directory; the form is five digits."""
    from custom_components.powerplan.core.tariffs import countries  # noqa: PLC0415
    from custom_components.powerplan.core.tariffs.sources import sahkonhinta  # noqa: PLC0415
    from custom_components.powerplan.providers.tariffs import directory  # noqa: PLC0415
    from tests.builders.tariff_sources import FIXTURES, FixtureHttp  # noqa: PLC0415

    finland = countries.get("FI")
    assert finland is not None
    assert directory.valid(finland, "00100")
    assert not directory.valid(finland, "0010")
    http = FixtureHttp(
        {sahkonhinta.DSOS: (FIXTURES / "sahkonhinta" / "getdsocollection.json").read_bytes()}
    )
    place = await directory.place_for(http, finland, "00100")  # type: ignore[arg-type]
    assert "Helen Sähköverkko Oy" in place.grid_companies
