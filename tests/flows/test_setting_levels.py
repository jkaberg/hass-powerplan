"""D8 §5.16, §9 29, 30 - each setting in one place: the device page or the gear.

Levels 1–2 are entities; the gear flow reads them back and neither asks nor
re-derives them (amended INV-66). Adding an appliance still asks them as
starting values, strategy only where there is a choice and priority as
low/normal/high.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import (
    DOMAIN,
    ENTITY_SETTINGS,
    LOAD_PARAMS,
    LOAD_PRIORITY,
    SUBENTRY_LOAD,
)
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.load_entities import PARAM_NUMBERS
from tests.flows.test_entity_set import _house
from tests.flows.test_load_flow import _answer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.e2e.fake_house import FakeHouse

TRANSLATIONS = Path(__file__).parents[2] / "custom_components" / "powerplan" / "translations"


def _keys(schema: Any) -> set[str]:
    """Every field a form asks, sections flattened, `param_` and day suffixes dropped."""
    out: set[str] = set()
    for marker, selector in schema.schema.items():
        key = str(marker.schema)
        inner = getattr(selector, "schema", None)
        if inner is not None and hasattr(inner, "schema"):
            out |= _keys(inner)
            continue
        out.add(key.removeprefix("param_"))
    return out


async def _reconfigure(hass: HomeAssistant, site: MockConfigEntry, load_id: str) -> list[set[str]]:
    """Walk the gear flow on its defaults; return each step's fields."""
    result: dict[str, Any] = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD),
            context={"source": "reconfigure", "subentry_id": load_id},
        )
    )
    asked: list[set[str]] = []
    while result["type"] is FlowResultType.FORM:
        asked.append(_keys(result["data_schema"]))
        result = await _answer(hass, result, **result["data_schema"]({}))
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful", result
    return asked


def test_entity_settings_cover_every_knob_parameter() -> None:
    """`ENTITY_SETTINGS` names every parameter an appliance number, switch or time owns."""
    for description in PARAM_NUMBERS:
        assert description.param in set().union(*ENTITY_SETTINGS.values()), description.key
    for type_key in ("ev", "water_heater", "generic_switch"):
        assert "force_max_h" in ENTITY_SETTINGS[type_key], "`run_now_max`"
    for type_key in ("floor_heating", "heat_pump", "radiator", "water_heater"):
        assert "follow_presence" in ENTITY_SETTINGS[type_key]


@pytest.mark.inv("INV-66")
async def test_29_no_setting_is_both_an_entity_and_a_gear_question(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§9 29: the gear asks nothing the device page owns, strategy and priority included."""
    loads = await _house(hass, site, charger)
    for type_key, load_id in loads.items():
        steps = await _reconfigure(hass, site, load_id)
        if type_key == "ev":
            assert "capacity_kwh" in steps[0], "level 3 is still asked"
        for fields in steps:
            assert not fields & ENTITY_SETTINGS[type_key], (
                type_key,
                fields & ENTITY_SETTINGS[type_key],
            )
            assert not fields & {"strategy", "priority"}, (type_key, fields)


@pytest.mark.inv("INV-66")
async def test_29_a_rederive_leaves_every_level_1_2_value_where_it_was(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """§9 29's second half: "Bruk de re-utledede verdiene" changes no entity's state."""
    loads = await _house(hass, site, charger)
    ev_id = loads["ev"]
    registry = er.async_get(hass)
    charge_to = registry.async_get_entity_id(
        "number", DOMAIN, unique_id(site.entry_id, "charge_target", ev_id)
    )
    priority = registry.async_get_entity_id(
        "select", DOMAIN, unique_id(site.entry_id, "priority", ev_id)
    )
    assert charge_to is not None
    assert priority is not None
    await hass.services.async_call(
        "number", "set_value", {"entity_id": charge_to, "value": 90.0}, blocking=True
    )
    await hass.services.async_call(
        "select", "select_option", {"entity_id": priority, "option": "high"}, blocking=True
    )
    await hass.async_block_till_done()
    before = dict(site.subentries[ev_id].data[LOAD_PARAMS])

    await _reconfigure(hass, site, ev_id)

    after = site.subentries[ev_id].data
    for key in ENTITY_SETTINGS["ev"]:
        assert after[LOAD_PARAMS].get(key) == before.get(key), key
    assert after[LOAD_PRIORITY] == 45
    for entity_id, value in ((charge_to, "90.0"), (priority, "high")):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == value


async def test_the_add_flow_asks_strategy_only_where_there_is_a_choice(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """D-0412: no `always` among the choices; the sauna, with one, is not asked."""
    from tests.flows.test_load_flow import _add_charger  # noqa: PLC0415 - test-local

    result = await _add_charger(hass, site, charger)
    strategy = next(
        selector
        for marker, selector in result["data_schema"].schema.items()
        if str(marker.schema) == "strategy"
    )
    assert strategy.config["options"] == ["deadline_fill", "cheapest_hours"]
    priority = next(
        selector
        for marker, selector in result["data_schema"].schema.items()
        if str(marker.schema) == "priority"
    )
    assert priority.config["options"] == ["low", "normal", "high"]

    sauna = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
        )
    )
    sauna = await _answer(hass, sauna, type="generic_switch")
    sauna = await _answer(hass, sauna, device=charger.loads["sauna"].device_id)
    sauna = await _answer(hass, sauna, **sauna["data_schema"]({}))
    sauna = await _answer(hass, sauna, **{**sauna["data_schema"]({}), "power_w": 6.0})
    assert sauna["step_id"] == "review", sauna
    assert "strategy" not in _keys(sauna["data_schema"])


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (
            "nb",
            (
                "Apparat",
                "Legg til apparat",
                "Endre oppsett",
                "Sikringskurs",
                "Gruppe",
                "Rom med flere varmekilder",
            ),
        ),
        (
            "en",
            (
                "Appliance",
                "Add appliance",
                "Change setup",
                "Circuit",
                "Group",
                "Room with several heat sources",
            ),
        ),
    ],
)
def test_30_the_integration_page_speaks_the_households_words(
    language: str, expected: tuple[str, ...]
) -> None:
    """§9 30: the sub-entry types and the gear's label (`ha-config-sub-entry-row.ts`)."""
    subentries = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))[
        "config_subentries"
    ]
    got = (
        subentries["load"]["entry_type"],
        subentries["load"]["initiate_flow"]["user"],
        subentries["load"]["initiate_flow"]["reconfigure"],
        subentries["circuit"]["entry_type"],
        subentries["group"]["entry_type"],
        subentries["zone"]["entry_type"],
    )
    assert got == expected
