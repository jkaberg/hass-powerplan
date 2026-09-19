"""D12 §9 1–6, 8: the dashboard's layout, its action (v0.8: `powerplan.get_dashboard`) and its entities.

The builder is tested on plain `SiteLayout`s - a `nordic_detached`-shaped
site of twelve loads for the golden, one load per D4 type for the tiles - and
through Home Assistant for what the registry and the runtime give it: the
action (`powerplan.get_dashboard`, D12 §5.16 R1), `calendar.<site>_plan`, `sensor.<site>_plan`'s slots and
`sensor.<site>_metric`.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.powerplan.calendar import plan_events
from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.loads import ActionReason
from custom_components.powerplan.core.metering import AnchorKind, ClosedWindow
from custom_components.powerplan.core.model import Confidence, Mode, Money, Plan, PlanMode, PlanSlot
from custom_components.powerplan.dashboard.config import texts_from
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
#: What `powerplan.get_dashboard` hands `build`: the dashboard's words, entity names, strategies.
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
    config = build(
        [site], "2026.3.0", TEXTS["nb"], language="nb", grid_statistics=["sensor.grid_import"]
    )
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
        "opening_direction": "right",
        "vertical_opening_direction": "up",
    }
    # no statistics graph; PowerPlan's own cards follow the picker.
    assert "statistics-graph" not in {card["type"] for card in _cards({"views": [history]})}
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
    """Config order is phone order; dense placement tidies the desktop (D12 §5.1, §5.12)."""
    t = EN
    config = build([_nordic()], "2026.3.0", t, grid_statistics=["sensor.grid_import"])
    assert _headings(_view(config, "overview")) == [
        None, t["section_hour"], t["section_price"], t["section_plan"], t["section_appliances"],
        t["section_capacity"], t["section_month"],
    ]  # fmt: skip
    assert _headings(_view(config, "history")) == [
        t["section_summary"], t["section_usage"], t["section_capacity"],
        t["section_cost_per_appliance"], t["section_events"],
    ]  # fmt: skip
    assert _headings(_subview(config, "tank")) == [
        t["section_control"], t["section_appliance_plan"], t["section_why"], t["section_month"],
    ]  # fmt: skip


def test_09_column_span_sits_on_the_section() -> None:
    """Price spans 2, Plan and Appliances 3, on the section dict and never on a card (B2, §5.12)."""
    now = _view(build([_nordic()], "2026.3.0", EN), "overview")
    assert _section(now, EN["section_price"])["column_span"] == 2
    assert _section(now, EN["section_plan"])["column_span"] == 3
    appliances = _section(now, EN["section_appliances"])
    assert appliances["column_span"] == 3
    [lanes] = [card for card in appliances["cards"] if card["type"] != "heading"]
    assert lanes["type"] == "custom:powerplan-appliances-card"
    assert lanes["grid_options"] == {"columns": "full", "rows": "auto"}
    assert [load["status"] for load in lanes["loads"]] == [
        f"sensor.{i}_plan_status" for i in ALL_LOADS
    ]
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


def test_09_the_appliance_row_opens_its_dialog_and_page() -> None:
    """A row's dialog reads the appliance's own entities and links its subview (D12 §5.12 R7)."""
    config = build([_nordic()], "2026.3.0", EN)
    lanes = _section(_view(config, "overview"), EN["section_appliances"])["cards"][1]
    rows = {load["id"]: load for load in lanes["loads"]}
    tank = rows["tank"]
    assert tank["path"] == f"{DASHBOARD}/appliance-tank"
    assert tank["status"] == "sensor.tank_plan_status"
    assert tank["kind"] == "water_heater"
    assert tank["icon"] == TYPE_ICONS["water_heater"]
    # (F2): the card's own names for the appliance's entities.
    for key, source in (
        ("control", "control"),
        ("cost", "cost_month"),
        ("legionella", "next_legionella"),
    ):
        assert tank[key] == f"{_domain(source)}.tank_{source}"
    assert rows["ev"]["deadline"] == "time.ev_ready_by"
    assert (
        lanes["rail_width"]
        == _section(_view(config, "overview"), EN["section_plan"])["cards"][1]["rail_width"]
    )


def test_09_hidden_appliances_drop_every_subview() -> None:
    """`hidden_views: [appliances]`: no subviews, and a row's dialog links none (D12 §6)."""
    config = build([_nordic()], "2026.3.0", EN, hidden_views=["appliances"])
    assert [view["path"] for view in config["views"]] == ["overview", "history"]
    lanes = _section(_view(config, "overview"), EN["section_appliances"])["cards"][1]
    assert not any("path" in load for load in lanes["loads"])
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
    assert tiles[load.entities["plan_status"]]["state_content"] == ["state", "reason_key"]
    for key, feature in TYPE_TILES[kind].items():
        tile = tiles[load.entities[key]]
        assert [f["type"] for f in tile.get("features", [])] == ([feature] if feature else [])
    money = [card["entity"] for card in cards if card["type"] == "entity"]
    assert load.entities["cost_month"] in money
    assert ("savings_month" in load.entities) == (load.entities.get("savings_month") in money)
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


def test_02_granted_power_draws_on_the_subview_only() -> None:
    """The lanes replaced Now's power split (§5.12); `granted_power` stays a subview tile."""
    load = _load("x", "ev", (*TYPE_KEYS["ev"], "granted_power"))
    config = build([_site((load,))], "2026.3.0", EN)
    assert "distribution" not in {card["type"] for card in _cards(config)}
    now = {card.get("entity") for card in _cards({"views": [_view(config, "overview")]})}
    assert "sensor.x_granted_power" not in now
    assert "sensor.x_granted_power" in {card.get("entity") for card in _subview_cards(config, "x")}


# --------------------------------------------------------------------------- #
# §9 3 - versions
# --------------------------------------------------------------------------- #


def test_03_below_the_releases_that_have_them_the_builder_degrades() -> None:
    """2026.1: the picker a card; 2026.3.0 the footer (D12 §5.5). The attention card needs no release (F13)."""
    load = _load("x", "ev")
    old = build([_site((load,))], "2026.1.0", EN)
    assert "custom:powerplan-attention-card" in {card["type"] for card in _cards(old)}
    history = _view(old, "history")
    assert "footer" not in history
    assert history["sections"][0]["cards"][0]["type"] == "energy-date-selection"
    floor = build([_site((load,))], "2026.3.0", EN)
    assert "repairs" not in {card["type"] for card in _cards(floor)}
    assert "footer" in _view(floor, "history")


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
    # (F13): one attention card explains the meter; it hides itself while all is well.
    [attention] = _view(build([_nordic()], "2026.3.0", EN), "overview")["sections"][0]["cards"]
    assert attention == {
        "type": "custom:powerplan-attention-card",
        "meter_status": "sensor.home_meter_health",
        "grid_options": {"columns": "full", "rows": "auto"},
    }


def test_14_the_capacity_step_is_the_month_gauge() -> None:
    """Now's capacity section: the window card in `mode: month` on the site's rows (D12 §5.3)."""
    now = _view(build([_nordic()], "2026.3.0", EN), "overview")
    cards = _section(now, EN["section_capacity"])["cards"]
    gauge = next(card for card in cards if card["type"] == "custom:powerplan-window-card")
    assert gauge["mode"] == "month"
    assert gauge["entities"] == {
        key: f"{_domain(key)}.home_{key}"
        for key in ("metric", "level", "projected_level", "advice", "target")
    }
    assert gauge["grid_options"] == {"columns": 12, "rows": 6}


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
    used |= {
        key if key.startswith("summary_") else f"card_{key}"
        for key in re.findall(r"labels\??\.(\w+)", cards)
    }
    used |= {f"card_stage_{word}" for word in ("normal", "tight", "critical")}  # `stageWord`
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
    # (§5.17 S1): `installStrategies` defines it, by the tag it is given, at the top level.
    assert '"ll-strategy-dashboard-powerplan"' in module
    assert "customElements.define(" in module
    assert "__ppStrategies" in module
    assert not re.search(r'(?:^|[;}])\s*import\s*["{\w*]', module)
    assert "import(" in module


# --------------------------------------------------------------------------- #
# §9 4 - powerplan.get_dashboard, through HA (§5.16 R1)
# --------------------------------------------------------------------------- #


def _call(**data: Any) -> dict[str, Any]:
    """Return the message `strategy.ts` sends: HA's own `call_service`, answered."""
    return {
        "type": "call_service",
        "domain": DOMAIN,
        "service": "get_dashboard",
        "service_data": data,
        "return_response": True,
    }


async def test_04_the_action_returns_the_site_layout(
    hass: HomeAssistant,
    site: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """A signed-in user who is not an admin gets the site's own entity ids back (D12 §9 4)."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id(_call(language="nb"))
    reply = await client.receive_json()
    assert reply["success"], reply
    config = reply["result"]["response"]
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


async def test_04_the_action_answers_what_the_builder_builds(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The action is the builder, nothing added (the golden does not move, §5.16 R1)."""
    from custom_components.powerplan.dashboard.config import (  # noqa: PLC0415
        async_dashboard_config,
    )

    answer = await hass.services.async_call(
        DOMAIN,
        "get_dashboard",
        {"site": SITE_ENTRY_ID, "hidden_views": ["history"]},
        blocking=True,
        return_response=True,
    )
    direct = await async_dashboard_config(
        hass, [site.runtime_data], language="en", hidden_views=["history"], hidden_cards=[]
    )
    assert answer == direct
    assert [view["path"] for view in direct["views"] if not view.get("subview")] == ["overview"]


async def test_04_an_unknown_site_is_an_error(
    hass: HomeAssistant, site: MockConfigEntry, hass_ws_client: WebSocketGenerator
) -> None:
    """Never an empty dashboard (D12 §9 4)."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(_call(site="nope"))
    reply = await client.receive_json()
    assert not reply["success"]
    assert reply["error"]["code"] == "service_validation_error"
    await client.send_json_auto_id(_call(hidden_views=[{"not": "a view"}]))
    reply = await client.receive_json()
    assert not reply["success"]
    assert reply["error"]["code"] == "invalid_format"
    await client.send_json_auto_id({**_call(), "return_response": False})
    reply = await client.receive_json()
    assert not reply["success"]


async def test_04_twenty_loads_build_in_under_100_ms(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The registry read and the build for a 20-load site stay under 100 ms (D12 §9 4)."""
    kinds = sorted(TYPE_KEYS)
    loads = tuple(_load(f"l{i}", kinds[i % len(kinds)]) for i in range(20))
    started = time.perf_counter()
    site_layout(hass, site)
    build([_site(loads)], "2026.3.0", EN, grid_statistics=["sensor.grid_import"])
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
        # Who stands still there on purpose, by the plan's own envelope (D-0507, INV-30).
        paused = row["paused"]
        for load_id, plan in runtime.state.plans.plans.items():
            found = plan.slot_at(datetime.fromisoformat(row["start"]))
            assert (load_id in paused) == (found is not None and found.envelope_w == 0.0)
    assert state.attributes["window_min"] == runtime.build.cfg.window_min
    assert state.state_info is not None
    assert "slots" in state.state_info["unrecorded_attributes"]


async def test_06_a_slot_s_baseline_is_only_what_d10_offers(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """Below the offer confidence a slot's `baseline_kwh` is `None`, the planner's own gate (D-0484).

    The house's timeline drew a 4.9 kW baseline at confidence 0.50 that neither
    the planner nor the budget used.
    """

    async def slots() -> list[dict[str, Any]]:
        await runtime.run_plan("test")
        await runtime.run_tick("test")
        await hass.async_block_till_done()
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "plan")
        )
        rows: list[dict[str, Any]] = hass.states.get(entity_id).attributes["slots"]
        assert rows
        return rows

    assert all(row["baseline_kwh"] is None for row in await slots())

    adapter = runtime.forecasts_adapter
    assert adapter is not None
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    # Eight weeks of quarter-hours at 1 kW: every bin well past the offer gate.
    for quarter in range(8 * 7 * 24 * 4, 0, -1):
        adapter.baseline.update(
            ClosedWindow(
                start_utc=now - timedelta(minutes=15 * quarter),
                window_min=15,
                kwh=0.25,
                avg_kw=1.0,
                anchor_kind=AnchorKind.REGISTER_LATCHED,
                degraded=False,
                confidence="exact",
            ),
            0.25,
        )

    for row in await slots():
        start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        hours = (end - start).total_seconds() / 3600
        assert row["baseline_kwh"] == pytest.approx(hours, abs=1e-3)


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


async def test_07_the_module_is_served_and_kept_as_a_resource(
    hass: HomeAssistant, hass_client: Any
) -> None:
    """The directory under `/powerplan_frontend`, the module keyed by its content (§5.5, §5.16 R4)."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA  # noqa: PLC0415
    from homeassistant.setup import async_setup_component  # noqa: PLC0415

    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "lovelace", {})
    assert await async_setup_component(hass, DOMAIN, {})
    urls = [item["url"] for item in hass.data[LOVELACE_DATA].resources.async_items()]
    module = next(url for url in urls if url.startswith("/powerplan_frontend/powerplan.js?v="))
    client = await hass_client()
    response = await client.get(module)
    assert response.status == 200
    body = await response.text()
    assert "ll-strategy-dashboard-powerplan" in body
    chunk = next(DIST.glob("chunks/chart-*.js"))
    assert (await client.get(f"/powerplan_frontend/chunks/{chunk.name}")).status == 200


# --------------------------------------------------------------------------- #
# §9 12 - the "why" card, in HA's template engine
# --------------------------------------------------------------------------- #

#: A day's plan slots from the reference house, 20:25 local (D12 §9 12).
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
    """Set the states the "why" card reads, as the reference house showed them."""
    await hass.config.async_set_time_zone("Europe/Oslo")
    freezer.move_to("2026-09-23T18:25:00+00:00")
    hass.states.async_set("sensor.home_plan", "4.68", {"slots": LIVE_SLOTS})
    hass.states.async_set(
        "sensor.tank_plan_status",
        "waiting",
        {
            "planned_kwh": 2.96, "deadline": "2026-09-24T04:00:00+00:00", "coverage": 1.0,
            "confidence": "known", "cost": "0.69 NOK", "strategy": "deadline_fill",
        },
    )  # fmt: skip
    return hass


def _markdown_in(config: dict[str, Any], view: dict[str, Any], heading: str) -> str:
    section = _section(view, heading)
    return next(card["content"] for card in section["cards"] if card["type"] == "markdown")


async def test_12_why_this_plan(live_house: HomeAssistant) -> None:
    """Need, the chosen runs, coverage and the strategy's own words (D12 §5.9)."""
    config = build([_nordic()], "2026.9.2", TEXTS["nb"], language="nb")
    why = _markdown_in(config, _subview(config, "tank"), TEXTS["nb"]["section_why"])
    text = await _render(live_house, why)
    assert "**Behov:** 2,96 kWh før 06:00" in text
    assert "**Valgt tid:** 22:00–22:30, 00:00–00:15" in text
    assert "**Dekning:** 100 % · kjente priser" in text
    assert f"≈ 0,69 kr · {TEXTS['nb']['strategy_deadline_fill']}" in text


# --------------------------------------------------------------------------- #
# §9 19 - polish (D12 §5.11)
# --------------------------------------------------------------------------- #


def test_19_tiles_and_badges_show_a_time_never_a_timestamp() -> None:
    """G5: the subview's next-run badge reads `next_run` and hides while running."""
    config = build([_nordic()], "2026.9.2", EN)
    badges = [
        b for b in _subview(config, "tank")["badges"] if b["entity"] == "sensor.tank_plan_status"
    ]
    time, running = badges[1], badges[2]
    assert time["state_content"] == ["next_run"]
    assert time["visibility"] == [
        {
            "condition": "state",
            "entity": "sensor.tank_plan_status",
            "state_not": ["charging", "running_plan", "run_now"],
        }
    ]
    assert running["name"] == EN["badge_running_now"]
    assert running["show_state"] is False
    assert running["visibility"][0]["state"] == ["charging", "running_plan", "run_now"]
    assert not any(
        "next_start" in str(card.get("state_content"))
        for card in (*_cards(config), *_badges(config))
    )


def test_19_the_month_s_money_is_the_sensor_s_own_state() -> None:
    """N7, A5, F9: Now's month is one card over the sensors' own state; the subview's `entity` cards."""
    config = build([_nordic()], "2026.9.2", EN)
    month = _section(_view(config, "overview"), EN["section_month"])["cards"]
    assert month[1:] == [
        {
            "type": "custom:powerplan-month-bars", "entity": "sensor.home_cost",
            "savings": "sensor.home_savings", "grid_options": {"columns": 12, "rows": 5},
        }
    ]  # fmt: skip
    sub = _section(_subview(config, "tank"), EN["section_month"])["cards"]
    assert [(c["type"], c["grid_options"]) for c in sub[1:]] == [
        ("entity", {"columns": 6, "rows": 2}),
        ("entity", {"columns": 6, "rows": 2}),
        ("statistic", {"columns": 12, "rows": 2}),
    ]
    assert not any(
        card["type"] == "statistic"
        and card["entity"].endswith(("_cost", "_savings", "_cost_month", "_savings_month"))
        for card in _cards(config)
    )


def test_19_lists_and_tables_are_powerplan_elements_not_markdown() -> None:
    """D5, D6: the per-appliance figures follow the style sheet; markdown only says why.

    Now's runs list (N8) went: the appliances card's lanes show every run (§5.12).
    """
    config = build([_nordic()], "2026.9.2", EN)
    assert "custom:powerplan-runs-card" not in {card["type"] for card in _cards(config)}
    history = _view(config, "history")
    table = _section(history, EN["section_cost_per_appliance"])  # the table only, full width
    assert table["column_span"] == 3
    table = table["cards"]
    assert table[1]["view"] == "table"
    assert table[1]["grid_options"] == {"columns": "full", "rows": "auto"}
    assert "title" not in table[1]
    assert "badges" not in table[0]
    assert table[1]["loads"][0] == {
        "id": "ev", "name": "Ev", "color": HA_GRAPH_PALETTE[0],
        "cost_month": "sensor.ev_cost_month", "savings_month": "sensor.ev_savings_month",
    }  # fmt: skip
    markdown = [card for card in _cards(config) if card["type"] == "markdown"]
    assert {card["content"].split("\n", 1)[0] for card in markdown} == {
        f'{{%- set e = "sensor.{i}_plan_status" -%}}' for i in ALL_LOADS
    }


def test_19_history_names_its_graphs_and_opens_its_picker_upward() -> None:
    """D1, D7: the picker opens as the Energy dashboard's does (no cost-and-savings graph)."""
    history = _view(build([_nordic()], "2026.9.2", EN), "history")
    assert history["footer"]["card"]["vertical_opening_direction"] == "up"
    peaks = _section(history, EN["section_capacity"])["cards"][1]
    assert peaks["grid_options"] == {"columns": 12, "rows": "auto"}
    assert set(peaks["entities"]) == {"window_used", "ceiling", "advice", "level", "target"}


def test_19_the_subview_s_ready_by_row_and_plan() -> None:
    """A2, A4, A6: the row has a clock icon and its own height; no bare kWh badge; "why" is auto-height."""
    config = build([_nordic()], "2026.9.2", EN)
    sub = _subview(config, "tank")
    ready = next(
        c for c in _section(sub, EN["section_control"])["cards"] if c["type"] == "entities"
    )
    assert ready["entities"][0]["icon"] == "mdi:clock-check-outline"
    assert ready["grid_options"] == {"columns": 12, "rows": "auto"}
    assert "badges" not in _section(sub, EN["section_appliance_plan"])["cards"][0]
    why = _section(sub, EN["section_why"])["cards"][1]
    assert why["grid_options"] == {"columns": "full", "rows": "auto"}


#: A tank that wants heat and draws none: `plan_status` is `waiting` (D8 §5.16).
_WAITING: dict[str, Any] = {
    "health": SimpleNamespace(unhealthy=False),
    "mode": Mode.AUTO,
    "demand": SimpleNamespace(wants=True),
    "measured_w": None,
}


def _runtime(now: datetime, next_start: datetime | None) -> SimpleNamespace:
    """Return the runtime `plan_status_attributes` reads: one tank plan, nothing overridden."""
    plan = SimpleNamespace(
        next_start=next_start, planned_kwh=7.603, cost=None, mode=PlanMode.PRICE, covered=True,
        coverage=1.0, deadline=datetime(2026, 9, 24, 4, 0, tzinfo=UTC), strategy="deadline_fill",
        confidence=Confidence.KNOWN,
    )  # fmt: skip
    return SimpleNamespace(
        snapshot=SimpleNamespace(at=now, plans={"tank": plan}),
        overridden_at={},
        state=SimpleNamespace(loads={}),
    )


def test_19_plan_status_carries_the_next_run_and_the_deadline_as_clock_times() -> None:
    """G5: local HH:MM; a run in progress has no next run; no plan, no attribute."""
    from custom_components.powerplan.load_entities import plan_status_attributes  # noqa: PLC0415

    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Oslo"))
    try:
        now = datetime(2026, 9, 23, 20, 12, 11, tzinfo=UTC)
        status = SimpleNamespace(
            load_id="tank", granted_w=0.0, action_reason="", action_key=None, action_params={},
            shed=False, shed_reason=None, latches=SimpleNamespace(shed_since=None), blunt=False,
            comfort=None, type_key="water_heater", **_WAITING,
        )  # fmt: skip

        def attributes(next_start: datetime | None) -> dict[str, Any]:
            return plan_status_attributes(status, _runtime(now, next_start))  # type: ignore[arg-type]

        later = attributes(datetime(2026, 9, 23, 20, 0, tzinfo=UTC).replace(hour=21))
        assert (later["next_run"], later["deadline_time"]) == ("23:00", "06:00")
        assert attributes(now)["next_run"] == ""
        assert attributes(None)["next_run"] == ""
        assert later["next_start"] == "2026-09-23T21:00:00+00:00"
    finally:
        dt_util.set_default_time_zone(UTC)


def test_19_a_waiting_load_s_reason_is_its_planned_run() -> None:
    """D-0630: waiting for 22:00, the reason is the run ahead, not the gate's "already at 45"."""
    from custom_components.powerplan.load_entities import (  # noqa: PLC0415
        PLANNED_REASON,
        plan_status_attributes,
    )

    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Oslo"))
    try:
        now = datetime(2026, 9, 24, 19, 6, 3, tzinfo=UTC)
        ahead = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)

        def attributes(granted_w: float, next_start: datetime | None) -> dict[str, Any]:
            status = SimpleNamespace(
                load_id="tank", granted_w=granted_w, action_reason="already at 45.0",
                action_key=ActionReason.ALREADY_AT, action_params={"value": 45.0}, shed=False,
                shed_reason=None, latches=SimpleNamespace(shed_since=None), blunt=False, comfort=None,
                type_key="water_heater", **_WAITING,
            )  # fmt: skip
            return plan_status_attributes(status, _runtime(now, next_start))  # type: ignore[arg-type]

        waiting = attributes(0.0, ahead)
        assert waiting["reason_key"] == PLANNED_REASON
        assert waiting["reason_params"] == {"time": "22:00", "kwh": 7.6}
        assert waiting["reason"] == "planned from 22:00 · 7.6 kWh"
        running = attributes(3_000.0, ahead)
        assert running["reason_key"] == ActionReason.ALREADY_AT.value, "acting: the gate's reason"
        nothing_ahead = attributes(0.0, None)
        assert nothing_ahead["reason_key"] == ActionReason.ALREADY_AT.value
    finally:
        dt_util.set_default_time_zone(UTC)


# --------------------------------------------------------------------------- #
# §9 20 - the price card's slots, the forecast's reserve, Now's order
# --------------------------------------------------------------------------- #


async def test_20_a_slot_s_p90_is_the_baseline_plus_d10_s_sigma(
    hass: HomeAssistant, site: MockConfigEntry, runtime: Runtime
) -> None:
    """`baseline_p90_kwh` = baseline + P90_Z·σ over the slot, `None` without a baseline (F2, D-0494)."""
    from custom_components.powerplan.runtime import P90_Z  # noqa: PLC0415

    async def slots() -> list[dict[str, Any]]:
        await runtime.run_plan("test")
        await runtime.run_tick("test")
        await hass.async_block_till_done()
        entity_id = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, unique_id(SITE_ENTRY_ID, "plan")
        )
        rows: list[dict[str, Any]] = hass.states.get(entity_id).attributes["slots"]
        assert rows
        return rows

    assert all(row["baseline_p90_kwh"] is None for row in await slots())
    adapter = runtime.forecasts_adapter
    assert adapter is not None
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    # Eight weeks of quarter-hours alternating 0.8 and 1.2 kW: a mean of 1 kW with a spread.
    for quarter in range(8 * 7 * 24 * 4, 0, -1):
        kw = 0.8 if quarter % 2 else 1.2
        adapter.baseline.update(
            ClosedWindow(
                start_utc=now - timedelta(minutes=15 * quarter),
                window_min=15,
                kwh=kw / 4,
                avg_kw=kw,
                anchor_kind=AnchorKind.REGISTER_LATCHED,
                degraded=False,
                confidence="exact",
            ),
            kw / 4,
        )
    rows = await slots()
    for row in rows:
        start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        sigma_w = adapter.baseline.residual_sigma(start)
        assert sigma_w is not None
        hours = (end - start).total_seconds() / 3600
        expected = row["baseline_kwh"] + P90_Z * sigma_w * hours / 1000
        assert row["baseline_p90_kwh"] == pytest.approx(expected, abs=2e-3)
    assert any(row["baseline_p90_kwh"] > row["baseline_kwh"] for row in rows)


def _price_slot(total: str, **components: str) -> Any:
    from custom_components.powerplan.core.model import Slot  # noqa: PLC0415

    start = datetime(2026, 9, 23, 21, tzinfo=UTC)
    return Slot(
        start=start,
        end=start + timedelta(minutes=15),
        total=Decimal(total),
        components={name: Decimal(value) for name, value in components.items()},
        confidence=Confidence.KNOWN,
    )


def test_20_a_price_slot_carries_its_energy_part_and_its_price_without_the_fixed_price() -> None:
    """P3: Norgespris 0.40 + VAT 25 % + grid 0.18 → energy 0.50, grid 0.23; spot 0.834 → 1.27 (D-0495)."""
    from custom_components.powerplan.core.model import (  # noqa: PLC0415
        Carrier,
        Direction,
        PriceCurve,
    )
    from custom_components.powerplan.sensor import _slots  # noqa: PLC0415

    def curve(slot: Any) -> PriceCurve:
        return PriceCurve(
            carrier=Carrier.ELECTRICITY,
            direction=Direction.IMPORT,
            currency="NOK",
            slots=(slot,),
            built_at=slot.start,
            sources=("nordpool",),
        )

    fixed = _price_slot("0.7300", spot="0.40", grid_energy="0.184", vat="0.146")
    spot = _price_slot("1.2725", spot="0.834", grid_energy="0.184", vat="0.2545")
    [row] = _slots(curve(fixed), reference=curve(spot), energy_vat=Decimal("0.25"))
    assert float(row["energy"]) == pytest.approx(0.50, abs=1e-4)
    assert float(row["total"]) - float(row["energy"]) == pytest.approx(0.23, abs=1e-4)
    assert row["reference"] == "1.2725"
    [plain] = _slots(curve(fixed))
    assert "reference" not in plain
    no_vat = _slots(curve(_price_slot("0.584", spot="0.40", grid_energy="0.184")))[0]
    assert float(no_vat["energy"]) == pytest.approx(0.40)
    # The reference house (D-0581): VAT on the energy only, the grid entered with its VAT.
    # The total ÷ (total − VAT) scaling read 0,45142; Norgespris is 0,50.
    house = _price_slot("0.8779", spot="0.40", grid_energy="0.3779", vat="0.10")
    [live] = _slots(curve(house), energy_vat=Decimal("0.25"))
    assert live["energy"] == "0.50000"


def test_20_now_is_price_plan_appliances_and_the_rails_line_up() -> None:
    """The price card beside the hour; the Plan card's rail equals the appliances card's (R2)."""
    config = build([_nordic()], "2026.9.2", EN)
    now = _view(config, "overview")
    price = _section(now, EN["section_price"])["cards"]
    assert "badges" not in price[0]  # the curve's tomorrow half says it
    assert price[1]["type"] == "custom:powerplan-price-card"
    assert price[1]["entities"] == {
        "price": "sensor.home_price",
        "price_forecast": "sensor.home_price_forecast",
    }
    plan = _section(now, EN["section_plan"])["cards"][1]
    lanes = _section(now, EN["section_appliances"])["cards"][1]
    assert plan["rail_width"] == lanes["rail_width"] == 256  # F13
    assert plan["bucket"] == "window"
    assert plan["show"] == ["plan", "baseline", "reserve", "ceiling"]  # no price track
    month = _section(now, EN["section_month"])["cards"]
    assert month[-1]["type"] == "custom:powerplan-month-bars"
    assert month[-1]["entity"] == "sensor.home_cost"
    assert {card["type"] for card in _cards({"views": [now]})} >= {
        "custom:powerplan-price-card",
        "custom:powerplan-appliances-card",
    }
    assert "tile" not in {card["type"] for card in _section(now, EN["section_appliances"])["cards"]}


def test_20_the_fixed_price_saving_is_each_hour_s_kwh_times_what_spot_would_have_cost_more() -> (
    None
):
    """P1, D-0499: spot 0.834 → 1.0425 with VAT, Norgespris 0.50; 2 kWh at 00–01 save 1.085."""
    from custom_components.powerplan.core.pricing import PriceContext, RawSlot  # noqa: PLC0415
    from custom_components.powerplan.core.pricing.holidays import NoHolidays  # noqa: PLC0415
    from custom_components.powerplan.core.pricing.modifiers.fixed_price import (  # noqa: PLC0415
        FixedPrice,
    )
    from custom_components.powerplan.core.pricing.modifiers.vat import Vat  # noqa: PLC0415
    from custom_components.powerplan.runtime import fixed_price_saving  # noqa: PLC0415

    since = datetime(2026, 8, 31, 22, tzinfo=UTC)
    today = datetime(2026, 9, 22, 22, tzinfo=UTC)

    def window(start: datetime, kwh: float) -> ClosedWindow:
        return ClosedWindow(
            start_utc=start, window_min=60, kwh=kwh, avg_kw=kwh,
            anchor_kind=AnchorKind.REGISTER_LATCHED, degraded=False, confidence="exact",
        )  # fmt: skip

    def raw(start: datetime, spot: str) -> RawSlot:
        return RawSlot(
            start=start, end=start + timedelta(hours=1), value=Decimal(spot), currency="NOK",
            source="nordpool", fetched_at=start,
        )  # fmt: skip

    ctx = PriceContext(
        now=today, tz=UTC, currency="NOK", mtd_kwh_at=lambda _t: 0.0, ytd_kwh_at=lambda _t: 0.0,
        day_type_at=lambda _d: None, holidays=NoHolidays(),
    )  # fmt: skip
    chain = (FixedPrice(price=Decimal("0.40")), Vat(rate=Decimal("0.25")))
    yesterday = today - timedelta(hours=2)
    saving = fixed_price_saving(
        [window(today, 2.0), window(yesterday, 1.0), window(today + timedelta(hours=5), 3.0)],
        [raw(today, "0.834"), raw(yesterday, "0.30")],
        chain,
        ctx,
        since=since,
        today=today,
    )
    assert saving.today == pytest.approx(2.0 * (0.834 * 1.25 - 0.50), abs=0.01)
    # Yesterday spot was the cheaper: a negative saving counts too; an unpriced hour does not.
    assert saving.month == pytest.approx(saving.today + 1.0 * (0.30 * 1.25 - 0.50), abs=0.01)
    assert saving.kwh == 3.0
    assert saving.since == since


def test_20_display_status_holds_a_flap_and_shows_a_hand_at_once() -> None:
    """R5, D-0497: TV-stua's `running_plan` ⇄ `paused_peak` shows only after 90 s; a hand at once."""
    from custom_components.powerplan.load_entities import DISPLAY_HOLD, held_status  # noqa: PLC0415

    holds: dict[str, Any] = {}
    t0 = datetime(2026, 9, 23, 21, tzinfo=UTC)
    assert held_status(holds, "tv", "running_plan", t0) == "running_plan"
    assert held_status(holds, "tv", "paused_peak", t0 + timedelta(seconds=10)) == "running_plan"
    assert held_status(holds, "tv", "running_plan", t0 + timedelta(seconds=40)) == "running_plan"
    assert held_status(holds, "tv", "paused_peak", t0 + timedelta(seconds=50)) == "running_plan"
    assert (
        held_status(holds, "tv", "paused_peak", t0 + timedelta(seconds=50) + DISPLAY_HOLD)
        == "paused_peak"
    )
    assert (
        held_status(holds, "tv", "manual_override", t0 + timedelta(minutes=5)) == "manual_override"
    )
