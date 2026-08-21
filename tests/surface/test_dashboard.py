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
    DASHBOARD,
    HA_GRAPH_PALETTE,
    build,
    load_colors,
)
from custom_components.powerplan.dashboard.site_layout import (
    TYPE_ICONS,
    LoadLayout,
    SiteLayout,
    site_layout,
)
from custom_components.powerplan.dashboard.ws import WS_TYPE, texts_from
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


def _flat(tree: dict[str, Any], prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


RAW = {
    language: json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))
    for language in ("en", "nb")
}
#: What the websocket command hands `build`: the dashboard's words, entity names, strategies.
TEXTS = {
    language: texts_from(_flat(tree, f"component.{DOMAIN}.")) for language, tree in RAW.items()
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
    "events", "meter_health", "replan",
)  # fmt: skip
#: Where each type's own tile lands, and with which feature.
TYPE_TILES = {
    "ev": {"charge_target": "numeric-input", "charge_min": "numeric-input"},
    "floor_heating": {"comfort": "numeric-input", "follow_presence": "toggle"},
    "heat_pump": {"follow_presence": "toggle"},
    "radiator": {"comfort": "numeric-input", "follow_presence": "toggle"},
    "water_heater": {"next_legionella": None},
    "appliance_cycle": {"run_now": "button"},
    "battery": {"charge_target": "numeric-input", "soc": "bar-gauge"},
    "generic_switch": {"hours_per_day": "numeric-input"},
}


def _domain(key: str) -> str:
    return {
        "control": "select", "presence": "select", "target": "select", "active": "switch",
        "follow_presence": "switch", "run_now": "button", "ready_by": "time",
        "charge_target": "number", "charge_min": "number", "comfort": "number",
        "hours_per_day": "number", "peak_warning": "binary_sensor",
        "prices_tomorrow": "binary_sensor", "plan_calendar": "calendar", "events": "event",
        "replan": "button",
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
    loads: tuple[LoadLayout, ...],
    entry_id: str = "site",
    name: str = "Home",
    keys: tuple[str, ...] = SITE_KEYS,
    **flags: bool,
) -> SiteLayout:
    return SiteLayout(
        entry_id=entry_id,
        name=name,
        currency="NOK",
        entities={key: f"{_domain(key)}.{name.lower()}_{key}" for key in keys},
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


def _badges(config: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for view in config["views"]:
        yield from view.get("badges", ())
        for card in _cards({"views": [view]}):
            yield from card.get("badges", ())


def _referenced(card: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    if "entity" in card:
        ids.add(card["entity"])
    entities = card.get("entities", ())
    rows = entities.values() if isinstance(entities, dict) else entities
    ids.update(row["entity"] if isinstance(row, dict) else row for row in rows)
    ids.update(card.get("target", {}).get("entity_id", ()))
    return ids


def _view(config: dict[str, Any], path: str) -> dict[str, Any]:
    return next(view for view in config["views"] if view["path"] == path)


def _headings(view: dict[str, Any]) -> list[str | None]:
    return [
        section["cards"][0]["heading"] if section["cards"][0]["type"] == "heading" else None
        for section in view["sections"]
    ]


def _section(view: dict[str, Any], heading: str) -> dict[str, Any]:
    return next(s for s in view["sections"] if s["cards"][0].get("heading") == heading)


def _subview(config: dict[str, Any], load_id: str) -> dict[str, Any]:
    return _view(config, f"appliance-{load_id}".lower())


def _subview_cards(config: dict[str, Any], load_id: str) -> list[dict[str, Any]]:
    return [card for section in _subview(config, load_id)["sections"] for card in section["cards"]]


# --------------------------------------------------------------------------- #
# §9 1 - the builder golden
# --------------------------------------------------------------------------- #


def test_01_golden_on_a_nordic_detached_site(snapshot: SnapshotAssertion) -> None:
    """Twelve loads: every reference shown, only the two custom cards, the Energy shape (D12 §9 1)."""
    site = _nordic()
    config = build([site], "2026.3.0", TEXTS["nb"], language="nb", has_energy_grid=True)
    shown = set(site.entities.values()) | {
        entity for load in site.loads for entity in load.entities.values()
    }
    for card in (*_cards(config), *_badges(config)):
        assert card["type"] in BUILT_IN | CUSTOM_CARDS | {"entity", "button"}, card["type"]
        assert _referenced(card) <= shown, card
    for view in config["views"]:
        assert view["type"] == "sections"
        assert view["max_columns"] == 3
        assert view["dense_section_placement"] is True
    history = _view(config, "history")
    assert history["footer"]["card"] == {
        "type": "energy-date-selection",
        "collection_key": COLLECTION_KEY,
    }
    graphs = [card for card in _cards({"views": [history]}) if card["type"] == "statistics-graph"]
    assert graphs
    for graph in graphs:
        assert graph["energy_date_selection"] is True
        assert graph["collection_key"] == COLLECTION_KEY
    assert config == snapshot


# --------------------------------------------------------------------------- #
# §9 9 - the views
# --------------------------------------------------------------------------- #


def test_09_two_tabs_and_a_subview_per_appliance() -> None:
    """`overview`, `history`, then one `appliance-<id>` per appliance, back to Now (D12 §5.1)."""
    config = build([_nordic()], "2026.3.0", EN)
    paths = [view["path"] for view in config["views"]]
    assert paths == ["overview", "history", *(f"appliance-{i}".lower() for i in ALL_LOADS)]
    for view in config["views"][:2]:
        assert "icon" not in view
        assert "subview" not in view
    for view in config["views"][2:]:
        assert view["subview"] is True
        assert view["back_path"] == f"{DASHBOARD}/overview"
        assert view["icon"] in TYPE_ICONS.values()


def test_09_sections_come_in_the_phone_order() -> None:
    """Config order is phone order; dense placement tidies the desktop (D12 §5.1)."""
    t = EN
    config = build([_nordic()], "2026.3.0", t, has_energy_grid=True)
    assert _headings(_view(config, "overview")) == [
        None, t["section_hour"], t["section_plan"], t["section_appliances"], t["section_capacity"],
        t["section_month"], t["section_next_runs"],
    ]  # fmt: skip
    assert _headings(_view(config, "history")) == [
        t["section_summary"], t["section_usage"], t["section_capacity"], t["section_per_appliance"],
        t["section_cost_per_appliance"], t["section_cost_savings"], t["section_events"],
    ]  # fmt: skip
    assert _headings(_subview(config, "tank")) == [
        t["section_control"], t["section_appliance_plan"], t["section_why"], t["section_month"],
    ]  # fmt: skip


def test_09_column_span_sits_on_the_section() -> None:
    """Plan spans 3, Appliances 2, on the section dict and never on a card (B2)."""
    now = _view(build([_nordic()], "2026.3.0", EN), "overview")
    assert _section(now, EN["section_plan"])["column_span"] == 3
    appliances = _section(now, EN["section_appliances"])
    assert appliances["column_span"] == 2
    tiles = [card for card in appliances["cards"] if card["type"] == "tile"]
    assert len(tiles) == len(ALL_LOADS)
    assert {tile["grid_options"]["columns"] for tile in tiles} == {12}
    for view in build([_nordic()], "2026.3.0", EN)["views"]:
        for card in _cards({"views": [view]}):
            assert "column_span" not in card


def test_09_no_name_carries_the_site_or_the_device() -> None:
    """Every card and badge names the thing itself, never "Home" or "Planstatus" (D12 §5.1)."""
    config = build([_nordic()], "2026.3.0", TEXTS["nb"], language="nb")
    words = [
        card.get("name") or card.get("heading") or card.get("title") or ""
        for card in (*_cards(config), *_badges(config))
    ]
    assert words
    for word in words:
        assert "Home" not in word
        assert "Planstatus" not in word
    tiles = [card for card in _cards(config) if card["type"] == "tile"]
    assert all("name" in tile for tile in tiles)


def test_09_the_appliance_tile_opens_its_page() -> None:
    """Tap → the subview, hold → more-info; the car shows its deadline (D12 §5.1)."""
    config = build([_nordic()], "2026.3.0", EN)
    tiles = {
        card["entity"]: card
        for card in _section(_view(config, "overview"), EN["section_appliances"])["cards"]
        if card["type"] == "tile"
    }
    tank = tiles["sensor.tank_plan_status"]
    assert tank["tap_action"] == {
        "action": "navigate",
        "navigation_path": f"{DASHBOARD}/appliance-tank",
    }
    assert tank["hold_action"] == {"action": "more-info"}
    assert "icon" not in tank
    assert tank["state_content"] == ["state", "next_start"]
    assert tiles["sensor.ev_plan_status"]["state_content"] == ["state", "deadline"]


def test_09_hidden_appliances_drop_every_subview() -> None:
    """`hidden_views: [appliances]`: no subviews, and a tile opens more-info (D12 §6)."""
    config = build([_nordic()], "2026.3.0", EN, hidden_views=["appliances"])
    assert [view["path"] for view in config["views"]] == ["overview", "history"]
    tiles = [
        card
        for card in _section(_view(config, "overview"), EN["section_appliances"])["cards"]
        if card["type"] == "tile"
    ]
    assert {tile["tap_action"]["action"] for tile in tiles} == {"more-info"}
    hidden = build([_nordic()], "2026.3.0", EN, hidden_views=["history"], hidden_cards=["markdown"])
    assert "history" not in [view["path"] for view in hidden["views"]]
    assert "markdown" not in {card["type"] for card in _cards(hidden)}


def test_09_several_sites_prefix_titles_and_suffix_paths() -> None:
    """One dashboard for every site; one site keeps the plain names (D-0439)."""
    home = _site((_load("ev", "ev"),), entry_id="a", name="Home")
    cabin = _site((_load("sauna", "generic_switch"),), entry_id="b", name="Cabin")
    config = build([home, cabin], "2026.3.0", EN)
    paths = [view["path"] for view in config["views"]]
    assert paths == [
        "overview-a",
        "history-a",
        "appliance-a-ev",
        "overview-b",
        "history-b",
        "appliance-b-sauna",
    ]
    assert config["views"][3]["title"] == "Cabin · Now"
    assert config["views"][5]["back_path"] == f"{DASHBOARD}/overview-b"
    one = build([home], "2026.3.0", EN)
    assert [view["title"] for view in one["views"]] == ["Now", "History", "Ev"]


# --------------------------------------------------------------------------- #
# §9 2 - per type, on the appliance's page
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", sorted(TYPE_KEYS))
def test_02_each_type_gets_its_tiles(kind: str) -> None:
    """Control, status, the type's own knobs, the month's figures (D12 §5.1, D8 §5.16)."""
    load = _load("x", kind)
    cards = _subview_cards(build([_site((load,))], "2026.3.0", EN), "x")
    tiles = {card["entity"]: card for card in cards if card["type"] == "tile"}
    control = tiles[load.entities["control"]]
    assert control["features"] == [{"type": "select-options"}]
    assert control["features_position"] == "inline"
    assert load.entities["plan_status"] in tiles
    for key, feature in TYPE_TILES[kind].items():
        tile = tiles[load.entities[key]]
        assert [f["type"] for f in tile.get("features", [])] == ([feature] if feature else [])
    statistics = [card["entity"] for card in cards if card["type"] == "statistic"]
    assert load.entities["cost_month"] in statistics
    assert ("savings_month" in load.entities) == (load.entities.get("savings_month") in statistics)
    rows = [card for card in cards if card["type"] == "entities"]
    assert bool(rows) == ("ready_by" in load.entities)
    timeline = next(card for card in cards if card["type"] == "custom:powerplan-timeline-card")
    assert [row["id"] for row in timeline["loads"]] == ["x"]
    assert timeline["entities"]["deadline"] == load.entities["plan_status"]


def test_02_a_disabled_or_absent_entity_is_left_out() -> None:
    """A floor loop whose comfort a device setpoint owns has no comfort tile (D8 §5.16)."""
    keys = tuple(key for key in TYPE_KEYS["floor_heating"] if key != "comfort")
    config = build([_site((_load("x", "floor_heating", keys),))], "2026.3.0", EN)
    assert "number.x_comfort" not in {card.get("entity") for card in _subview_cards(config, "x")}


def test_02_granted_power_draws_where_the_household_enabled_it() -> None:
    """`granted_power` is off by default: the power split appears only when a load shows it."""
    plain = build([_site((_load("x", "ev"),))], "2026.3.0", EN)
    assert "distribution" not in {card["type"] for card in _cards(plain)}
    load = _load("x", "ev", (*TYPE_KEYS["ev"], "granted_power"))
    enabled = build([_site((load,))], "2026.3.0", EN)
    distribution = next(card for card in _cards(enabled) if card["type"] == "distribution")
    assert distribution["entities"] == [
        {"entity": "sensor.x_granted_power", "name": "X", "color": HA_GRAPH_PALETTE[0]}
    ]


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
    appliances = _section(_view(old, "overview"), EN["section_appliances"])
    assert "entities" in {card["type"] for card in appliances["cards"]}

    floor = build([_site((load,))], "2026.3.0", EN)
    types = {card["type"] for card in _cards(floor)}
    assert {"distribution", "repairs"} <= types
    assert "footer" in _view(floor, "history")
    between = build([_site((load,))], "2026.2.1", EN)
    types = {card["type"] for card in _cards(between)}
    assert "distribution" in types
    assert "repairs" not in types


# --------------------------------------------------------------------------- #
# §9 10 - one colour per appliance
# --------------------------------------------------------------------------- #


def test_10_one_colour_per_appliance_on_every_card() -> None:
    """The same hex in tile, split, timeline, graph and badges; per site; stable (D12 §5.8)."""
    loads = tuple(
        _load(load_id, NORDIC_TYPES[load_id], (*TYPE_KEYS[NORDIC_TYPES[load_id]], "granted_power"))
        for load_id in ALL_LOADS
    )
    config = build([_site(loads)], "2026.6.0", EN)
    colors = load_colors(loads)
    assert list(colors.values()) == list(HA_GRAPH_PALETTE[: len(loads)])
    seen: dict[str, set[str]] = {load.subentry_id: set() for load in loads}
    by_entity = {entity: load.subentry_id for load in loads for entity in load.entities.values()}
    for card in (*_cards(config), *_badges(config)):
        if card.get("entity") in by_entity and "color" in card:
            seen[by_entity[card["entity"]]].add(card["color"])
        for row in card.get("entities", ()) if isinstance(card.get("entities"), list) else ():
            if isinstance(row, dict) and row.get("entity") in by_entity and "color" in row:
                seen[by_entity[row["entity"]]].add(row["color"])
        for row in card.get("loads", ()):
            seen[row["id"]].add(row["color"])
    for load_id, found in seen.items():
        assert found == {colors[load_id]}, load_id
    assert build([_site(loads)], "2026.6.0", EN) == config
    other = build([_site(loads[3:], entry_id="b")], "2026.6.0", EN)
    assert load_colors(loads[3:])[loads[3].subentry_id] == HA_GRAPH_PALETTE[0]
    assert other != config


def test_10_below_2026_6_graphs_carry_no_colour() -> None:
    """`statistics-graph` reads `entities[].color` from HA 2026.6 (D12 §5.5)."""
    config = build([_nordic()], "2026.5.0", EN)
    for card in _cards(config):
        if card["type"] == "statistics-graph":
            for row in card["entities"]:
                assert not isinstance(row, dict) or "color" not in row


# --------------------------------------------------------------------------- #
# §9 11 - conditionals
# --------------------------------------------------------------------------- #


def test_11_sections_follow_what_the_site_has() -> None:
    """No tariff, no grid source, no appliances, a healthy meter (D12 §5.1)."""
    no_tariff = tuple(key for key in SITE_KEYS if key not in {"metric", "level"})
    now = _view(build([_site((_load("x", "ev"),), keys=no_tariff)], "2026.3.0", EN), "overview")
    assert EN["section_capacity"] not in _headings(now)
    history = _view(build([_nordic()], "2026.3.0", EN), "history")
    assert EN["section_usage"] not in _headings(history)
    empty = _view(build([_site(())], "2026.3.0", EN), "overview")
    appliances = _section(empty, EN["section_appliances"])
    assert appliances["cards"][1] == {"type": "markdown", "content": EN["no_loads"]}
    meter = next(
        card
        for card in _view(build([_nordic()], "2026.3.0", EN), "overview")["sections"][0]["cards"]
        if card.get("entity") == "sensor.home_meter_health"
    )
    assert meter["visibility"] == [
        {"condition": "state", "entity": "sensor.home_meter_health", "state_not": "ok"}
    ]


def test_11_the_logbook_follows_every_appliance() -> None:
    """The site's events and every `plan_status`, where `logbook.py` files them (D-0453)."""
    history = _view(build([_nordic()], "2026.3.0", EN), "history")
    logbook = next(card for card in _cards({"views": [history]}) if card["type"] == "logbook")
    assert logbook["target"]["entity_id"] == [
        "event.home_events",
        *(f"sensor.{load_id}_plan_status" for load_id in ALL_LOADS),
    ]
    assert logbook["hours_to_show"] == 48


# --------------------------------------------------------------------------- #
# §9 18 - the texts
# --------------------------------------------------------------------------- #

LAYOUT = Path(__file__).parents[2] / "custom_components" / "powerplan" / "dashboard" / "layout.py"
FRONTEND = Path(__file__).parents[2] / "frontend" / "src"


def test_18_every_text_is_used_and_exists_in_both_languages() -> None:
    """No key missing in either file, and none left over from the old views (D12 §9 18)."""
    import re  # noqa: PLC0415

    options = {lang: tree["selector"]["dashboard"]["options"] for lang, tree in RAW.items()}
    assert options["en"].keys() == options["nb"].keys()
    source = LAYOUT.read_text("utf-8")
    used = set(re.findall(r"""(?:\bt|texts)\[["'](\w+)["']\]""", source))
    used |= {f"view_{view}" for view in ("overview", "history")}
    used |= {f"confidence_{c}" for c in ("known", "stale", "estimated", "synthesised")}
    cards = "".join(path.read_text("utf-8") for path in FRONTEND.glob("*.ts"))
    used |= {f"card_{key}" for key in re.findall(r"labels\.(\w+)", cards)}
    assert used <= options["en"].keys(), used - options["en"].keys()
    assert options["en"].keys() <= used, options["en"].keys() - used


def test_18_every_strategy_has_its_words() -> None:
    """The "why" table names each strategy D5 registers, in both languages (D12 §5.9)."""
    from custom_components.powerplan.core.strategies import keys  # noqa: PLC0415

    for language in ("en", "nb"):
        missing = {key for key in keys() if f"strategy_{key}" not in TEXTS[language]}
        assert not missing, (language, missing)


def test_18_the_strategy_is_defined_before_anything_is_fetched() -> None:
    """`powerplan.js` imports nothing statically: HA waits 5 s for the element (B1)."""
    import re  # noqa: PLC0415

    module = (DIST / "powerplan.js").read_text("utf-8")
    assert 'customElements.define("ll-strategy-dashboard-powerplan"' in module
    assert not re.search(r'(?:^|[;}])\s*import\s*["{\w*]', module)
    assert "import(" in module


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
    assert config["views"][0]["title"] == "Nå"
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


# --------------------------------------------------------------------------- #
# §9 12 - the three tables, in HA's template engine
# --------------------------------------------------------------------------- #

#: The reference house's plan one evening at 20:25 local (D12 §9 12): four appliances
#: from 22:00, the rest nothing; the car charging towards 06:00.
LIVE_BY_LOAD = {
    "tank": {"planned_kwh": 2.96, "cost": "0.69 NOK", "next_start": "2026-09-23T20:00:00+00:00"},
    "loop_hall": {"planned_kwh": 1.056, "cost": "0.77 NOK", "next_start": "2026-09-23T20:00:00+00:00"},
    "loop_bath_1": {"planned_kwh": 0.447, "cost": "0.33 NOK", "next_start": "2026-09-23T20:00:00+00:00"},
    "loop_bath_2": {"planned_kwh": 0.22, "cost": "0.16 NOK", "next_start": "2026-09-23T22:00:00+00:00"},
    "sauna": {"planned_kwh": 0.0, "cost": "0.00 NOK", "next_start": None},
}  # fmt: skip
LIVE_SLOTS = [
    {"start": "2026-09-23T20:00:00+00:00", "end": "2026-09-23T20:15:00+00:00", "planned_kwh": {"tank": 0.74}},
    {"start": "2026-09-23T20:15:00+00:00", "end": "2026-09-23T20:30:00+00:00", "planned_kwh": {"tank": 0.74}},
    {"start": "2026-09-23T20:30:00+00:00", "end": "2026-09-23T20:45:00+00:00", "planned_kwh": {}},
    {"start": "2026-09-23T22:00:00+00:00", "end": "2026-09-23T22:15:00+00:00", "planned_kwh": {"tank": 0.5}},
]  # fmt: skip


async def _render(hass: HomeAssistant, content: str) -> str:
    from homeassistant.helpers.template import Template  # noqa: PLC0415

    return str(Template(content, hass).async_render(parse_result=False))


@pytest.fixture
async def live_house(hass: HomeAssistant, freezer: Any) -> HomeAssistant:
    """Set the states the three tables read, as the reference house showed them."""
    await hass.config.async_set_time_zone("Europe/Oslo")
    freezer.move_to("2026-09-23T18:25:00+00:00")
    hass.states.async_set(
        "sensor.home_plan", "4.68", {"by_load": LIVE_BY_LOAD, "slots": LIVE_SLOTS}
    )
    hass.states.async_set(
        "sensor.ev_plan_status", "charging", {"deadline": "2026-09-24T04:00:00+00:00"}
    )
    hass.states.async_set(
        "sensor.tank_plan_status",
        "waiting",
        {
            "planned_kwh": 2.96, "deadline": "2026-09-24T04:00:00+00:00", "coverage": 1.0,
            "confidence": "known", "cost": "0.69 NOK", "strategy": "deadline_fill",
        },
    )  # fmt: skip
    for load_id, cost, saved in (
        ("loop_kitchen", "0.42", "-0.42"),
        ("tank", "0", "2.16"),
        ("ev", "0.23", "-0.23"),
    ):
        hass.states.async_set(f"sensor.{load_id}_cost_month", cost)
        hass.states.async_set(f"sensor.{load_id}_savings_month", saved)
    return hass


def _markdown_in(config: dict[str, Any], view: dict[str, Any], heading: str) -> str:
    section = _section(view, heading)
    return next(card["content"] for card in section["cards"] if card["type"] == "markdown")


async def test_12_next_runs_in_norwegian(live_house: HomeAssistant) -> None:
    """Rows by start, comma decimals, the sum, and the car running now (D12 §5.9)."""
    config = build([_nordic()], "2026.8.0", TEXTS["nb"], language="nb")
    text = await _render(
        live_house,
        _markdown_in(config, _view(config, "overview"), TEXTS["nb"]["section_next_runs"]),
    )
    lines = [line for line in text.splitlines() if line.startswith("|")]
    assert lines[0] == "| Start | Apparat | kWh | NOK |"
    assert lines[2:] == [
        "| 22:00 | Tank | 2,96 | 0,69 |",
        "| 22:00 | Loop Hall | 1,06 | 0,77 |",
        "| 22:00 | Loop Bath 1 | 0,45 | 0,33 |",
        "| tor 00:00 | Loop Bath 2 | 0,22 | 0,16 |",
        "| **Sum** | | **4,68** | **1,95** |",
    ]
    assert "Ev går nå · frist 06:00" in text
    assert "kWh og kr gjelder hele planen." in text


async def test_12_next_runs_in_english_and_empty(live_house: HomeAssistant) -> None:
    """Dot decimals in `en`; an empty plan says so (D12 §5.9)."""
    config = build([_nordic()], "2026.8.0", EN, language="en")
    content = _markdown_in(config, _view(config, "overview"), EN["section_next_runs"])
    text = await _render(live_house, content)
    assert "| 22:00 | Tank | 2.96 | 0.69 |" in text
    assert "| **Total** | | **4.68** | **1.95** |" in text
    assert "Thu 00:00" in text
    live_house.states.async_set("sensor.home_plan", "0", {"by_load": {}})
    assert EN["md_no_runs"] in await _render(live_house, content)


async def test_12_cost_per_appliance(live_house: HomeAssistant) -> None:
    """Dearest first, signed savings with a true minus in `nb` (D12 §5.9)."""
    config = build([_nordic()], "2026.8.0", TEXTS["nb"], language="nb")
    history = _view(config, "history")
    text = await _render(
        live_house, _markdown_in(config, history, TEXTS["nb"]["section_cost_per_appliance"])
    )
    rows = [line for line in text.splitlines() if line.startswith("|")][2:]
    assert rows[0] == "| Loop Kitchen | 0,42 | −0,42 |"
    assert rows[1] == "| Ev | 0,23 | −0,23 |"
    assert "| Tank | 0,00 | +2,16 |" in rows
    assert rows[-1] == "| **Sum** | **0,65** | **+1,51** |"


async def test_12_why_this_plan(live_house: HomeAssistant) -> None:
    """Need, the chosen runs, coverage and the strategy's own words (D12 §5.9)."""
    config = build([_nordic()], "2026.8.0", TEXTS["nb"], language="nb")
    why = _markdown_in(config, _subview(config, "tank"), TEXTS["nb"]["section_why"])
    text = await _render(live_house, why)
    assert "**Behov:** 2,96 kWh før 06:00" in text
    assert "**Valgt tid:** 22:00–22:30, 00:00–00:15" in text
    assert "**Dekning:** 100 % · kjente priser" in text
    assert f"≈ 0,69 NOK · {TEXTS['nb']['strategy_deadline_fill']}" in text
