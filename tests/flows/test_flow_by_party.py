"""The site flow by party: grid company, supplier, state (D13 §6, D8 §5.1, §5.17).

D13 §19 12 and 13; D8 §9 32, 33, 34 and 37. A fake source per country stands in
for the grid companies' (D9 §5.15); the postcode directory answers from
Kartverket's captured responses. Sockets stay closed throughout.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.const import CONF_POSTCODE, CONF_TARIFF
from custom_components.powerplan.core.tariffs import household
from custom_components.powerplan.core.tariffs.sources import (
    Credit,
    Operator,
    Product,
    Question,
    Tier,
    UnreachableError,
    fri_nettleie,
    nve,
)
from custom_components.powerplan.diagnostics import async_get_config_entry_diagnostics
from custom_components.powerplan.providers.tariffs import base
from custom_components.powerplan.providers.tariffs.fri_nettleie import FriNettleie
from tests.builders.tariff_sources import FRI_ARCHIVE, NVE_COUNTIES, fake
from tests.flows.test_site_flow import (
    ELECTRICAL_NO,
    _answer,
    _configure,
    _start,
    _tail,
    _through_meter,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant

KARTVERKET = Path(__file__).resolve().parents[1] / "fixtures" / "tariff_sources" / "kartverket"
ELVIA = Operator("elvia", "Elvia", zones=("", "nord"))
TENSIO = Operator(
    "tensio-ts", "Tensio TS", products=(Product("bolig", "Bolig"), Product("hytte", "Hytte"))
)
CREDIT = Credit("Fri Nettleie", "https://github.com/kraftsystemet/fri-nettleie", "CC BY 4.0")


@pytest.fixture
def source() -> Iterator[type]:
    """Register a Norwegian fake that lists Elvia and Tensio and asks one gap."""
    cls = fake(
        "fakenett",
        Tier.T1B,
        operators=(ELVIA, TENSIO),
        licence="CC BY 4.0",
        credit=CREDIT,
        questions=(Question("window_min", 60, "The source does not say how long a window is."),),
    )
    base.register(cls)
    yield cls
    base.unregister("fakenett")


@pytest.fixture
def directory(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer Kartverket from its captured responses, and record every URL asked."""
    asked: list[str] = []

    async def get(self: Any, url: str, **headers: str) -> bytes:
        asked.append(url)
        if "postnummer=" in url:
            postcode = url.split("postnummer=")[1].split("&", maxsplit=1)[0]
            path = KARTVERKET / f"adresser-{postcode}.json"
        else:
            path = KARTVERKET / f"kommune-{url.rsplit('/', 1)[1]}.json"
        if not path.is_file():
            raise UnreachableError(url)
        return path.read_bytes()

    monkeypatch.setattr(base.Http, "get", get)
    return asked


@pytest.fixture
def fri(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, list[Any]]]:
    """Register fri-nettleie, answered from its captured archive and NVE's county list.

    Only the download is replaced: `Http`'s cache and its release run as shipped.
    """
    seen: dict[str, list[Any]] = {"downloaded": [], "released": []}

    async def download(self: Any, url: str, headers: Any) -> bytes:
        seen["downloaded"].append(url)
        if url == fri_nettleie.TARBALL:
            return FRI_ARCHIVE.read_bytes()
        if url.startswith(nve.URL.split("?", maxsplit=1)[0]):
            return NVE_COUNTIES.read_bytes()
        raise UnreachableError(url)

    release = base.Http.release

    def released(self: Any) -> None:
        seen["released"].append(len(self._cache))
        release(self)

    monkeypatch.setattr(base.Http, "_download", download)
    monkeypatch.setattr(base.Http, "release", released)
    base.register(FriNettleie)
    yield seen
    base.unregister(fri_nettleie.KEY)


def _options(result: dict[str, Any], field: str) -> list[str]:
    """Return a select's values, as the form offers them."""
    for key, selector in result["data_schema"].schema.items():
        if key == field:
            return [
                option if isinstance(option, str) else option["value"]
                for option in selector.config["options"]
            ]
    raise KeyError(field)


async def _to_postcode(hass: HomeAssistant, ams_meter: str) -> dict[str, Any]:
    _configure(hass)
    result = await _through_meter(hass, await _start(hass, "full"), ams_meter)
    assert result["step_id"] == "postcode"
    return result


async def _finish(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """From the grid summary to the entry, on every default."""
    for _ in range(30):
        if result["step_id"] == "presence":
            break
        answer = {"price": 100} if result["step_id"] == "prices_fixed" else {}
        result = await _answer(hass, result, **(answer or result["data_schema"]({})))
    result = await _tail(hass, result)
    return await _answer(hass, result, start_in_observe=True)


@pytest.mark.inv("INV-74")
async def test_32_a_fetched_grid_company_walks_product_zone_gaps_and_the_summary(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str], source: type
) -> None:
    """Tensio TS from the fake: product, county, the gap, the summary with credit and plan."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result)  # skipped: the county is asked instead

    assert result["step_id"] == "tariff"
    # D8 §9 37: the credit note, linked, with its licence (D13 §6.1).
    assert result["description_placeholders"]["credit"] == (
        "Grid tariffs from [Fri Nettleie](https://github.com/kraftsystemet/fri-nettleie)"
        " (CC BY 4.0). Thank you!"
    )
    options = next(
        value.config["options"]
        for value in result["data_schema"].schema.values()
        if hasattr(value, "config")
    )
    assert {"value": "operator:Tensio TS", "label": "Tensio TS"} in options
    result = await _answer(hass, result, preset="operator:Tensio TS")

    assert result["step_id"] == "tariff_product"
    assert result["description_placeholders"] == {"operator": "Tensio TS"}
    result = await _answer(hass, result, product="bolig")

    assert result["step_id"] == "tariff_confirm"
    assert result["data_schema"]({}) == {"window_min": 60}
    result = await _answer(hass, result, window_min=60)

    assert result["step_id"] == "tariff_preset"
    placeholders = result["description_placeholders"]
    assert placeholders["credit"].startswith("Grid tariffs from [Fri Nettleie]")
    # What the plan does with the grid's day/night charge, in the household's money.
    assert (
        "Flexible use moves to after 22:00, when the grid charge is 14 øre/kWh lower."
        in (placeholders["plan"])
    )
    result = await _answer(hass, result, confirm="yes")
    result = await _answer(hass, result, target="auto", risk="flat")

    assert result["step_id"] == "prices"
    result = await _answer(hass, result, source="nordpool_action")
    assert result["step_id"] == "modifiers", "one Nord Pool entry names its area"
    result = await _finish(hass, result)

    price = household.from_json(result["data"][CONF_TARIFF]["price"])
    assert price.grid.provenance.source == "fakenett"
    assert price.grid.provenance.tier == "T1b"
    assert price.grid.provenance.fetched is not None
    assert price.grid.renew_at is not None
    assert (price.grid.operator_key, price.grid.product_key) == ("tensio-ts", "bolig")
    assert price.confirmed == {"window_min": 60}
    assert result["data"][CONF_TARIFF]["preset_file"] is None


async def test_32_a_company_spanning_zones_asks_the_county_without_a_postcode(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str], source: type
) -> None:
    """Elvia spans the national rates and Nord-Norge's: the county is asked (step 1b)."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result)
    result = await _answer(hass, result, preset="operator:Elvia")
    assert result["step_id"] == "tariff_zone"
    assert result["data_schema"]({}) == {"zone": ""}
    result = await _answer(hass, result, zone="nord")
    assert result["step_id"] == "tariff_confirm"
    result = await _answer(hass, result, window_min=60)
    result = await _answer(hass, result, confirm="yes")
    result = await _answer(hass, result, target="auto", risk="flat")
    result = await _answer(hass, result, source="nordpool_action")
    result = await _answer(hass, result)  # the supplier's additions: none
    assert result["step_id"] == "state"
    # Nord-Norge pays no VAT on electricity: the step states it (D13 §9.1).
    assert "VAT 0 %" in result["description_placeholders"]["rates"]
    result = await _finish(hass, result)
    zone = household.from_json(result["data"][CONF_TARIFF]["price"]).state.zone
    assert (zone.key, zone.settled) == ("nord", "asked")


async def test_34_the_postcode_goes_to_kartverket_only_and_settles_the_zone(  # noqa: PLR0917 - fixtures
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    source: type,
    directory: list[str],
) -> None:
    """9060 Lyngen is in the tiltakssone; the county is then not asked (D13 §6 step 0, O17)."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result, postcode="9060")
    assert all(url.startswith("https://ws.geonorge.no/") for url in directory)
    assert len(directory) == 2
    assert result["step_id"] == "tariff"
    result = await _answer(hass, result, preset="operator:Elvia")
    assert result["step_id"] == "tariff_confirm", "the postcode settled the county"
    result = await _answer(hass, result, window_min=60)
    result = await _finish(hass, result)
    data = result["data"]
    zone = household.from_json(data[CONF_TARIFF]["price"]).state.zone
    assert (zone.key, zone.settled) == ("tiltakssone", "postcode")
    assert data[CONF_POSTCODE] == "9060"


async def test_34_an_unreachable_directory_skips_the_step(  # noqa: PLR0917 - fixtures
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    source: type,
    directory: list[str],
) -> None:
    """A postcode Kartverket cannot answer: the list and the county are asked (§13)."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result, postcode="1234")
    assert result["step_id"] == "tariff"
    result = await _answer(hass, result, preset="operator:Elvia")
    assert result["step_id"] == "tariff_zone"


async def test_34_a_malformed_postcode_is_refused_on_its_field(
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    directory: list[str],
) -> None:
    """Norway's postcodes are four digits; nothing is sent for anything else."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result, postcode="70 10x")
    assert result["errors"] == {"postcode": "postcode_invalid"}
    assert directory == []


async def test_15_no_tier_reachable_offers_the_template_and_custom(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """Every source down: `tariff_source_unreachable`, and the rest of the list stays (§13)."""
    base.register(fake("down", Tier.T1A, behaviour="down"))
    try:
        result = await _to_postcode(hass, ams_meter)
        result = await _answer(hass, result)
        assert result["step_id"] == "tariff"
        assert result["errors"] == {"base": "tariff_source_unreachable"}
        result = await _answer(hass, result, preset="custom")
        assert result["step_id"] == "tariff_preset"
    finally:
        base.unregister("down")


@pytest.mark.inv("INV-71")
async def test_12_in_norway_a_custom_tariff_asks_no_vat(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """Norway's module knows its VAT: no screen asks it (D13 §19 12)."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result)
    result = await _answer(hass, result, preset="custom")
    seen: list[dict[str, Any]] = []
    for _ in range(30):
        if result["step_id"] == "presence":
            break
        seen.append(result["data_schema"].schema)
        answer = result["data_schema"]({}) if result["step_id"] != "prices_fixed" else {"price": 1}
        result = await _answer(hass, result, **answer)
    assert not any(str(key) == "vat" for schema in seen for key in schema), (
        "no VAT question in Norway"
    )


@pytest.mark.inv("INV-71")
async def test_12_in_the_us_the_vat_is_asked_and_kept_as_the_households(
    hass: HomeAssistant, persons: list[str]
) -> None:
    """The US module has no national rate: the state step asks it (§9.1, 1c-prime)."""
    hass.config.time_zone = "America/Chicago"
    hass.config.currency = "USD"
    hass.config.country = "US"
    result = await _start(hass, "price_only")
    while result["step_id"] != "state":
        answer = {"price": 12} if result["step_id"] == "prices_fixed" else result["data_schema"]({})
        result = await _answer(hass, result, **answer)
    assert "vat" in result["data_schema"]({})
    result = await _answer(hass, result, vat=7.5)
    result = await _finish(hass, result)
    price = household.from_json(result["data"][CONF_TARIFF]["price"])
    assert str(price.state.overrides["vat"]) == "0.075"


@pytest.mark.parametrize(
    ("zone", "country"),
    [("Europe/Oslo", "NO"), ("Atlantic/Canary", "ES"), ("Asia/Tokyo", "")],
)
async def test_13_without_a_country_the_time_zone_preselects_it(
    hass: HomeAssistant, zone: str, country: str
) -> None:
    """D13 §19 13: HA without a country; its zone pre-selects one, an unknown zone none."""
    hass.config.time_zone = zone
    hass.config.currency = "EUR"
    hass.config.country = None
    result = await _start(hass, "fuse_only")
    while result["step_id"] != "electrical":
        result = await _answer(hass, result, **result["data_schema"]({}))
    assert result["data_schema"]({}).get("country", "") == country


async def test_13_with_a_country_in_home_assistant_it_is_not_asked(
    hass: HomeAssistant, ams_meter: str
) -> None:
    """D8 §9 33: HA has the country; no screen asks it."""
    _configure(hass)
    result = await _start(hass, "full")
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, confirm="ok")
    assert result["step_id"] == "electrical"
    assert "country" not in result["data_schema"]({})
    result = await _answer(hass, result, **ELECTRICAL_NO)
    assert result["step_id"] == "postcode"


async def test_37_diagnostics_carry_the_copys_provenance_and_never_the_postcode(  # noqa: PLR0917 - fixtures
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    source: type,
    directory: list[str],
) -> None:
    """The source, its tier, the fetch date and the credit; the postcode redacted (D8 §5.17)."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result, postcode="7010")
    result = await _answer(hass, result, preset="operator:Elvia")
    result = await _answer(hass, result, window_min=60)
    result = await _finish(hass, result)
    await hass.async_block_till_done()
    entry = result["result"]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["tariff"]["source"] == "fakenett"
    assert diagnostics["tariff"]["tier"] == "T1b"
    assert diagnostics["tariff"]["credit"][0]["licence"] == "CC BY 4.0"
    assert "7010" not in str(diagnostics)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_1_tensio_tn_asks_the_county_and_the_flow_lets_its_downloads_go(
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    fri: dict[str, list[Any]],
) -> None:
    """D13 §19 1: NVE puts Tensio TN in Nordland and Trøndelag - the county is asked.

    The archive is downloaded once for the list and the copy, and released as soon
    as the copy is taken (§5.2 rule 6).
    """
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result)
    assert "operator:Tensio TN AS" in _options(result, "preset")
    result = await _answer(hass, result, preset="operator:Tensio TN AS")
    assert result["step_id"] == "tariff_zone"
    assert _options(result, "zone") == ["Nordland", "Trøndelag"]
    result = await _answer(hass, result, zone="Nordland")
    assert result["step_id"] == "tariff_preset"
    assert fri["downloaded"].count(fri_nettleie.TARBALL) == 1
    assert fri["released"] == [2], "the archive and NVE's list, dropped once the copy is taken"
    result = await _finish(hass, result)
    price = household.from_json(result["data"][CONF_TARIFF]["price"])
    assert (price.grid.provenance.source, price.grid.operator_key) == ("fri_nettleie", "tensio-tn")
    assert (price.state.zone.key, price.state.zone.settled) == ("nord", "asked")


async def test_1_elvia_serves_one_zone_and_asks_no_county(
    hass: HomeAssistant,
    ams_meter: str,
    nordpool_entry: str,
    persons: list[str],
    fri: dict[str, list[Any]],
) -> None:
    """Elvia is one tax zone in NVE's list: straight from the company to its summary."""
    result = await _to_postcode(hass, ams_meter)
    result = await _answer(hass, result)
    result = await _answer(hass, result, preset="operator:Elvia AS")
    assert result["step_id"] == "tariff_preset"
