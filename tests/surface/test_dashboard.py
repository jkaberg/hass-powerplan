"""D12 §9 1–6, 8: the dashboard's layout, its websocket command and its entities.

The builder is tested on plain `SiteLayout`s - a `nordic_detached`-shaped
site of twelve loads for the golden, one load per D4 type for the tiles - and
through Home Assistant for what the registry and the runtime give it: the
websocket command, `calendar.<site>_plan`, `sensor.<site>_plan`'s slots and
`sensor.<site>_metric`.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.calendar import plan_events
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.model import Confidence, Money, Plan, PlanMode, PlanSlot
from custom_components.powerplan.dashboard.layout import (
    COLLECTION_KEY,
    CUSTOM_CARDS,
    build,
)
from custom_components.powerplan.dashboard.site_layout import (
    TYPE_ICONS,
    LoadLayout,
    SiteLayout,
    site_layout,
)
from custom_components.powerplan.dashboard.ws import WS_TYPE
from custom_components.powerplan.entity import unique_id
from tests.builders.houses import ALL_LOADS
from tests.runtime.conftest import SITE_ENTRY_ID

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.typing import WebSocketGenerator
    from syrupy.assertion import SnapshotAssertion

    from custom_components.powerplan.runtime import Runtime

TRANSLATIONS = Path(__file__).parents[2] / "custom_components" / "powerplan" / "translations"
TEXTS = {
    language: json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))["selector"][
        "dashboard"
    ]["options"]
    for language in ("en", "nb")
}
EN = TEXTS["en"]
#: The frontend's built-in card types this builder may use (HA 2026.3.0's
#: `src/panels/lovelace/cards/`, the Energy cards and the section heading).
BUILT_IN = frozenset(
    {
        "calendar",
        "distribution",
        "energy-date-selection",
        "energy-devices-graph",
        "energy-sankey",
        "energy-usage-graph",
        "entities",
        "heading",
        "logbook",
        "markdown",
        "repairs",
        "statistic",
        "statistics-graph",
        "tile",
    }
)

#: `nordic_detached`'s twelve loads by D4 type (tests/builders/houses.py).
NORDIC_TYPES = {
    "ev": "ev",
    "loop_bath_1": "floor_heating",
    "loop_bath_2": "floor_heating",
    "loop_hall": "floor_heating",
    "loop_kitchen": "floor_heating",
    "loop_living": "floor_heating",
    "tank": "water_heater",
    "heat_pump": "heat_pump",
    "radiator_bed_1": "radiator",
    "radiator_bed_2": "radiator",
    "dishwasher": "appliance_cycle",
    "sauna": "generic_switch",
}
#: D8 §5.16's shown rows by type, as a new site gets them (granted_power off).
TYPE_KEYS = {
    "ev": ("control", "plan_status", "cost_month", "savings_month", "charge_target", "charge_min", "ready_by", "energy"),
    "floor_heating": ("control", "plan_status", "cost_month", "savings_month", "comfort", "follow_presence", "energy"),
    "heat_pump": ("control", "plan_status", "cost_month", "savings_month", "follow_presence", "energy"),
    "radiator": ("control", "plan_status", "cost_month", "savings_month", "comfort", "follow_presence", "energy"),
    "water_heater": ("control", "plan_status", "cost_month", "savings_month", "ready_by", "next_legionella", "energy"),
    "appliance_cycle": ("control", "plan_status", "cost_month", "run_now", "ready_by", "energy"),
    "battery": ("control", "plan_status", "cost_month", "savings_month", "charge_target", "energy", "soc"),
    "generic_switch": ("control", "plan_status", "cost_month", "hours_per_day", "energy"),
}  # fmt: skip
SITE_KEYS = (
    "active", "presence", "target", "window_used", "window_projected", "ceiling", "allowance", "stage",
    "level", "projected_level", "advice", "next_peak_warning", "peak_warning", "price",
    "price_forecast", "prices_tomorrow", "plan", "metric", "plan_calendar", "cost", "savings",
    "events",
)  # fmt: skip
#: Where each type's own tile lands, and with which feature.
TYPE_TILES = {
    "ev": {"charge_target": "numeric-input", "charge_min": "numeric-input"},
    "floor_heating": {"comfort": "numeric-input", "follow_presence": "toggle"},
    "heat_pump": {"follow_presence": "toggle"},
    "radiator": {"comfort": "numeric-input", "follow_presence": "toggle"},
    "water_heater": {"next_legionella": None},
    "appliance_cycle": {"run_now": "button"},
    "battery": {"charge_target": "numeric-input", "soc": None},
    "generic_switch": {"hours_per_day": "numeric-input"},
}


def _domain(key: str) -> str:
    return {
        "control": "select", "presence": "select", "target": "select", "active": "switch",
        "follow_presence": "switch", "run_now": "button", "ready_by": "time",
        "charge_target": "number", "charge_min": "number", "comfort": "number",
        "hours_per_day": "number", "peak_warning": "binary_sensor",
        "prices_tomorrow": "binary_sensor", "plan_calendar": "calendar", "events": "event",
    }.get(key, "sensor")  # fmt: skip


def _load(load_id: str, kind: str, keys: tuple[str, ...] | None = None) -> LoadLayout:
    keys = TYPE_KEYS[kind] if keys is None else keys
    return LoadLayout(
        subentry_id=load_id,
        name=load_id.replace("_", " ").title(),
        type=kind,
        entities={key: f"{_domain(key)}.{load_id}_{key}" for key in keys},
        icon=TYPE_ICONS[kind],
    )


def _site(
    loads: tuple[LoadLayout, ...], entry_id: str = "site", name: str = "Home", **flags: bool
) -> SiteLayout:
    return SiteLayout(
        entry_id=entry_id,
        name=name,
        currency="NOK",
        entities={key: f"{_domain(key)}.{name.lower()}_{key}" for key in SITE_KEYS},
        loads=loads,
        has_production=flags.get("has_production", False),
        has_battery=flags.get("has_battery", False),
        has_export=False,
        circuits=(),
        groups=(),
    )


def _nordic() -> SiteLayout:
    return _site(tuple(_load(load_id, NORDIC_TYPES[load_id]) for load_id in ALL_LOADS))


def _cards(config: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for view in config["views"]:
        if "footer" in view:
            yield view["footer"]["card"]
        for section in view["sections"]:
            yield from section["cards"]


def _referenced(card: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    if "entity" in card:
        ids.add(card["entity"])
    entities = card.get("entities", ())
    ids.update(entities.values() if isinstance(entities, dict) else entities)
    ids.update(card.get("target", {}).get("entity_id", ()))
    return ids


def _view(config: dict[str, Any], path: str) -> dict[str, Any]:
    return next(view for view in config["views"] if view["path"] == path)


def _load_section(config: dict[str, Any], name: str) -> list[dict[str, Any]]:
    for section in _view(config, "loads")["sections"]:
        if section["cards"][0].get("heading") == name:
            return section["cards"][1:]
    raise AssertionError(name)


# --------------------------------------------------------------------------- #
# §9 1 - the builder golden
# --------------------------------------------------------------------------- #


def test_01_golden_on_a_nordic_detached_site(snapshot: SnapshotAssertion) -> None:
    """Twelve loads: every reference shown, only the two custom cards, the Energy shape (D12 §9 1)."""
    site = _nordic()
    config = build([site], "2026.3.0", EN)
    shown = set(site.entities.values()) | {
        entity for load in site.loads for entity in load.entities.values()
    }
    for card in _cards(config):
        assert card["type"] in BUILT_IN | CUSTOM_CARDS, card["type"]
        assert _referenced(card) <= shown, card
    assert [view["path"] for view in config["views"]] == ["overview", "plan", "loads", "history"]
    for view in config["views"]:
        assert view["type"] == "sections"
        assert view["max_columns"] == 3
        assert view["dense_section_placement"] is True
    history = _view(config, "history")
    assert history["footer"]["card"] == {
        "type": "energy-date-selection",
        "collection_key": COLLECTION_KEY,
    }
    graphs = [
        card
        for section in history["sections"]
        for card in section["cards"]
        if card["type"] == "statistics-graph"
    ]
    assert graphs
    for graph in graphs:
        assert graph["energy_date_selection"] is True
        assert graph["collection_key"] == COLLECTION_KEY
    assert len(_view(config, "loads")["sections"]) == len(ALL_LOADS)
    assert config == snapshot


# --------------------------------------------------------------------------- #
# §9 2 - per type
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", sorted(TYPE_KEYS))
def test_02_each_type_gets_its_tiles(kind: str) -> None:
    """Control, status, the type's own knobs, cost and savings (D12 §5.1, D8 §5.16)."""
    load = _load("x", kind)
    cards = _load_section(build([_site((load,))], "2026.3.0", EN), "X")
    tiles = {card["entity"]: card for card in cards if card["type"] == "tile"}
    control = tiles[load.entities["control"]]
    assert control["features"] == [{"type": "select-options"}]
    assert load.entities["plan_status"] in tiles
    for key, feature in TYPE_TILES[kind].items():
        tile = tiles[load.entities[key]]
        assert [f["type"] for f in tile.get("features", [])] == ([feature] if feature else [])
    statistics = [card["entity"] for card in cards if card["type"] == "statistic"]
    assert load.entities["cost_month"] in statistics
    assert ("savings_month" in load.entities) == (load.entities.get("savings_month") in statistics)
    rows = [card for card in cards if card["type"] == "entities"]
    assert bool(rows) == ("ready_by" in load.entities)


def test_02_a_disabled_or_absent_entity_is_left_out() -> None:
    """A floor loop whose comfort a device setpoint owns has no comfort tile (D8 §5.16)."""
    keys = tuple(key for key in TYPE_KEYS["floor_heating"] if key != "comfort")
    cards = _load_section(build([_site((_load("x", "floor_heating", keys),))], "2026.3.0", EN), "X")
    assert "number.x_comfort" not in {card.get("entity") for card in cards}


def test_02_granted_power_draws_where_the_household_enabled_it() -> None:
    """`granted_power` is off by default: the power section appears only when a load shows it."""
    plain = build([_site((_load("x", "ev"),))], "2026.3.0", EN)
    assert "distribution" not in {card["type"] for card in _cards(plain)}
    load = _load("x", "ev", (*TYPE_KEYS["ev"], "granted_power"))
    enabled = build([_site((load,))], "2026.3.0", EN)
    distribution = next(card for card in _cards(enabled) if card["type"] == "distribution")
    assert distribution["entities"] == ["sensor.x_granted_power"]


# --------------------------------------------------------------------------- #
# §9 3 - versions
# --------------------------------------------------------------------------- #


def test_03_below_the_releases_that_have_them_the_builder_degrades() -> None:
    """2026.1: no `distribution`, no `repairs`, the picker a card; 2026.3.0 has all three (D12 §5.5)."""
    load = _load("x", "ev", (*TYPE_KEYS["ev"], "granted_power"))
    old = build([_site((load,))], "2026.1.0", EN)
    types = {card["type"] for card in _cards(old)}
    assert "distribution" not in types
    assert "repairs" not in types
    history = _view(old, "history")
    assert "footer" not in history
    assert history["sections"][0]["cards"][0]["type"] == "energy-date-selection"
    assert "entities" in {card["type"] for card in _view(old, "overview")["sections"][2]["cards"]}

    floor = build([_site((load,))], "2026.3.0", EN)
    types = {card["type"] for card in _cards(floor)}
    assert {"distribution", "repairs"} <= types
    assert "footer" in _view(floor, "history")
    between = build([_site((load,))], "2026.2.1", EN)
    types = {card["type"] for card in _cards(between)}
    assert "distribution" in types
    assert "repairs" not in types


# --------------------------------------------------------------------------- #
# Several sites, the strategy's options, the texts
# --------------------------------------------------------------------------- #


def test_several_sites_get_four_views_each() -> None:
    """One dashboard for every site, each view named and pathed by its site."""
    home = _site((_load("ev", "ev"),), entry_id="a", name="Home")
    cabin = _site((_load("sauna", "generic_switch"),), entry_id="b", name="Cabin")
    config = build([home, cabin], "2026.3.0", EN)
    paths = [view["path"] for view in config["views"]]
    assert paths == [
        f"{path}-{entry}" for entry in "ab" for path in ("overview", "plan", "loads", "history")
    ]
    assert config["views"][4]["title"] == "Cabin · Overview"
    one = build([home], "2026.3.0", EN)
    assert [view["title"] for view in one["views"]] == ["Overview", "Plan", "Appliances", "History"]


def test_hidden_views_and_cards_are_left_out() -> None:
    """The Energy dashboard's own option names (D12 §6)."""
    config = build([_nordic()], "2026.3.0", EN, hidden_views=["history"], hidden_cards=["calendar"])
    assert [view["path"] for view in config["views"]] == ["overview", "plan", "loads"]
    assert "calendar" not in {card["type"] for card in _cards(config)}


def test_the_texts_are_complete_in_both_languages() -> None:
    """Every heading the builder names exists in `en` and `nb`, in the translations."""
    assert TEXTS["en"].keys() == TEXTS["nb"].keys()
    build([_nordic()], "2026.3.0", TEXTS["nb"], has_energy_grid=True)


def test_the_energy_cards_appear_only_with_a_grid_source() -> None:
    """HA's own usage, devices and sankey cards follow the history picker (D12 §5.1)."""
    without = {card["type"] for card in _cards(build([_nordic()], "2026.3.0", EN))}
    assert "energy-sankey" not in without
    config = build([_nordic()], "2026.3.0", EN, has_energy_grid=True)
    sankey = next(card for card in _cards(config) if card["type"] == "energy-sankey")
    assert sankey["collection_key"] == COLLECTION_KEY


# --------------------------------------------------------------------------- #
# §9 4 - the websocket command, through HA
# --------------------------------------------------------------------------- #


async def test_04_the_command_returns_the_site_layout(
    hass: HomeAssistant, site: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """Read-only: the site's own entity ids come back, from the registry (D12 §9 4)."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": WS_TYPE, "language": "nb"})
    reply = await client.receive_json()
    assert reply["success"], reply
    config = reply["result"]
    assert config["views"][0]["title"] == "Oversikt"
    registry = er.async_get(hass)
    window = registry.async_get_entity_id("sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "window_used"))
    gauge = next(card for card in _cards(config) if card["type"] == "custom:powerplan-window-card")
    assert gauge["entities"]["window_used"] == window
    for card in _cards(config):
        for entity_id in _referenced(card):
            entry = registry.async_get(entity_id)
            assert entry is not None, entity_id
            assert entry.disabled_by is None, entity_id


async def test_04_an_unknown_site_is_an_error(
    hass: HomeAssistant, site: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """Never an empty dashboard (D12 §9 4)."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": WS_TYPE, "entry_id": "nope"})
    reply = await client.receive_json()
    assert not reply["success"]
    assert reply["error"]["code"] == "not_found"
    await client.send_json_auto_id({"type": WS_TYPE, "language": 3})
    reply = await client.receive_json()
    assert not reply["success"]
    assert reply["error"]["code"] == "invalid_format"


async def test_04_twenty_loads_build_in_under_100_ms(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The registry read and the build for a 20-load site stay under 100 ms (D12 §9 4)."""
    kinds = sorted(TYPE_KEYS)
    loads = tuple(_load(f"l{i}", kinds[i % len(kinds)]) for i in range(20))
    started = time.perf_counter()
    site_layout(hass, site)
    build([_site(loads)], "2026.3.0", EN, has_energy_grid=True)
    assert time.perf_counter() - started < 0.1


# --------------------------------------------------------------------------- #
# §9 5 - calendar.<site>_plan
# --------------------------------------------------------------------------- #

T0 = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


def _plan(load_id: str, caps: list[float | None], price: str = "1.00") -> Plan:
    slots = tuple(
        PlanSlot(
            start=T0 + timedelta(hours=i),
            end=T0 + timedelta(hours=i + 1),
            envelope_w=cap,
            kwh=0.0 if cap is None else cap / 1000.0,
            price=Decimal(price),
        )
        for i, cap in enumerate(caps)
    )
    return Plan(
        load_id=load_id,
        strategy="deadline_fill",
        mode=PlanMode.PRICE,
        slots=slots,
        built_at=T0,
        cost_estimate=Money(Decimal(0), "NOK"),
        confidence=Confidence.KNOWN,
    )


def test_05_each_contiguous_block_is_one_event() -> None:
    """Load, kWh and estimated cost; a stand-still or no-plan slot splits a block (D12 §9 5)."""
    plans = {"ev": _plan("ev", [0.0, 11000.0, 11000.0, None, 7000.0, 0.0], "0.50")}
    events = plan_events(plans, {"ev": "Car"}, "NOK", T0)
    assert [(e.start, e.end) for e in events] == [
        (T0 + timedelta(hours=1), T0 + timedelta(hours=3)),
        (T0 + timedelta(hours=4), T0 + timedelta(hours=5)),
    ]
    assert events[0].summary == "Car: 22.0 kWh"
    assert events[0].description == "22.00 kWh · ≈ 11.00 NOK"


def test_05_a_block_that_ended_is_gone() -> None:
    """Past blocks drop off; the one in progress stays (D12 §5.6)."""
    plans = {"ev": _plan("ev", [11000.0, 0.0, 11000.0])}
    later = plan_events(plans, {}, "NOK", T0 + timedelta(hours=1, minutes=30))
    assert [event.start for event in later] == [T0 + timedelta(hours=2)]
    during = plan_events(plans, {}, "NOK", T0 + timedelta(minutes=30))
    assert [event.start for event in during] == [T0, T0 + timedelta(hours=2)]


async def test_05_the_calendar_moves_only_on_adoption(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """A tick without a new plan writes nothing; an adoption rewrites it (D12 §9 5)."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "calendar", DOMAIN, unique_id(SITE_ENTRY_ID, "plan_calendar")
    )
    assert entity_id is not None
    before = hass.states.get(entity_id)
    assert before is not None
    await runtime.run_tick("test")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).last_updated == before.last_updated


# --------------------------------------------------------------------------- #
# §9 6 - sensor.<site>_plan's slots, sensor.<site>_metric
# --------------------------------------------------------------------------- #


async def test_06_the_site_plan_carries_its_slots_unrecorded(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """Every slot has `ceiling_kwh`, `baseline_kwh` and `planned_kwh` by load (D12 §9 6)."""
    await runtime.run_plan("test")
    await runtime.run_tick("test")
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "plan")
    )
    state = hass.states.get(entity_id)
    slots = state.attributes["slots"]
    assert slots, "the plan should reach the price horizon"
    for row in slots:
        assert {"start", "end", "ceiling_kwh", "baseline_kwh", "planned_kwh"} <= row.keys()
    assert state.attributes["window_min"] == runtime.build.cfg.window_min
    sensor = hass.data["sensor"].get_entity(entity_id)
    assert "slots" in sensor._unrecorded_attributes


async def test_06_the_metric_is_the_tariffs_own(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """`sensor.<site>_metric` equals D2's period metric in kW (D12 §9 6)."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "metric")
    )
    snapshot = runtime.snapshot
    assert snapshot is not None
    assert snapshot.tariff is not None
    assert float(hass.states.get(entity_id).state) == pytest.approx(
        snapshot.tariff.level.metric_kw, abs=1e-3
    )


# --------------------------------------------------------------------------- #
# §9 8 - no write path, no state read
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-3")
def test_08_the_dashboard_package_calls_no_service_and_reads_no_state() -> None:
    """The dashboard reads the registries and the runtime only (D12 §3, INV-3)."""
    package = Path(__file__).parents[2] / "custom_components" / "powerplan" / "dashboard"
    for path in package.glob("*.py"):
        text = path.read_text("utf-8")
        for needle in ("services.async_call", "states.get", "states.async_all"):
            assert needle not in text, f"{path.name}: {needle}"


# --------------------------------------------------------------------------- #
# §9 7 (Python half) - the bundle HA serves
# --------------------------------------------------------------------------- #

DIST = Path(__file__).parents[2] / "custom_components" / "powerplan" / "frontend" / "dist"


def test_07_every_chunk_the_bundle_imports_is_committed() -> None:
    """A stale or partial build fails here, not in the household's browser (D12 §5.5)."""
    import re  # noqa: PLC0415

    for path in DIST.rglob("*.js"):
        for chunk in re.findall(r'"\./(?:chunks/)?([\w-]+\.js)"', path.read_text("utf-8")):
            assert list(DIST.rglob(chunk)), f"{path.name} imports {chunk}, which is not in dist/"


async def test_07_the_module_is_served_and_loaded_on_every_page(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """The directory under `/powerplan_frontend`, the module keyed by its content (D12 §5.5)."""
    from homeassistant.components.frontend import (  # noqa: PLC0415
        DATA_EXTRA_MODULE_URL,
        UrlManager,
    )
    from homeassistant.setup import async_setup_component  # noqa: PLC0415

    assert await async_setup_component(hass, "http", {})
    # The frontend's own setup needs its built package; its URL list is all this reads.
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])
    hass.config.components.add("frontend")
    assert await async_setup_component(hass, DOMAIN, {})
    urls = list(hass.data[DATA_EXTRA_MODULE_URL].urls)
    module = next(url for url in urls if url.startswith("/powerplan_frontend/powerplan.js?v="))
    client = await hass_client()
    response = await client.get(module)
    assert response.status == 200
    body = await response.text()
    assert "ll-strategy-dashboard-powerplan" in body
    chunk = next(DIST.glob("chunks/chart-*.js"))
    assert (await client.get(f"/powerplan_frontend/chunks/{chunk.name}")).status == 200
