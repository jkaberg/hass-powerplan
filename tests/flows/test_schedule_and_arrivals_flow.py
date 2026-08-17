"""D8 §5.5's schedule/arrival questions and the follow-presence knob.

`schedule_entity`, `arrival_sources` and `switch.<load>_follow_presence`,
through the load flow (D4 §4.4, D-0300, D-0301). The tank of
`tests/e2e/fake_house.py` is reused from `test_water_heater_flow.py`'s
`_add_tank`: a generic-thermostat-shaped `climate` entity where `water_heater`
is a deliberate match-step answer, not a guess.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er

from custom_components.powerplan.const import DOMAIN, LOAD_PARAMS, SUBENTRY_LOAD
from custom_components.powerplan.entity import unique_id
from custom_components.powerplan.load_entities import PARAM_NUMBERS
from tests.flows.test_load_flow import _answer
from tests.flows.test_water_heater_flow import _add_tank

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse


async def test_an_answered_schedule_and_arrival_calendars_reach_params(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """Answering both questions materialises them into the subentry's `params`."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": "user"}
        )
    )
    device_id = charger.loads["tank"].device_id
    assert device_id is not None
    result = await _answer(hass, result, device=device_id)

    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    result = await _answer(hass, result, **{**suggested, "type": "water_heater"})

    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    # `schedule_entity`'s own default is `None`, so voluptuous omits the key
    # entirely rather than filling it in (`_marker`'s `vol.Optional(key)` with
    # no `default=`) - the same as every other unanswered singular `ENTITY`
    # question (`outdoor_entity`, `outlet_entity`, `temp_entity`). Only a
    # tuple-defaulted multi-entity question like `arrival_sources` survives
    # the round trip as its own key.
    assert "schedule_entity" not in defaults["advanced"]
    assert defaults["advanced"]["arrival_sources"] == []
    answers = {
        **defaults,
        "advanced": {
            **defaults["advanced"],
            "schedule_entity": "schedule.hot_water",
            "arrival_sources": ["calendar.partner", "calendar.household"],
        },
    }
    result = await _answer(hass, result, **answers)
    assert result["step_id"] == "review", result
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Tank"})
    await hass.async_block_till_done()

    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    params = sub.data[LOAD_PARAMS]
    assert params["schedule_entity"] == "schedule.hot_water"
    assert params["arrival_sources"] == ["calendar.partner", "calendar.household"]

    # The tank's own setpoint is its comfort knob (D8 §5.16), so there is no
    # PowerPlan comfort number to hide behind the schedule.
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "number", DOMAIN, unique_id(site.entry_id, "comfort", sub.subentry_id)
        )
        is None
    )


def test_the_comfort_knob_hides_behind_a_bound_schedule() -> None:
    """`comfort` (where no device setpoint owns it) is hidden once a schedule drives the target."""
    comfort = next(row for row in PARAM_NUMBERS if row.key == "comfort")
    scheduled = SimpleNamespace(config=SimpleNamespace(params={"schedule_entity": "schedule.x"}))
    unscheduled = SimpleNamespace(config=SimpleNamespace(params={}))
    assert comfort.visible(scheduled) is False  # type: ignore[arg-type]
    assert comfort.visible(unscheduled) is True  # type: ignore[arg-type]


async def test_the_follow_presence_switch_exists_disabled_by_default(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """`switch.<load>_follow_presence` is registered but disabled by default (D8 §5.5)."""
    result = await _add_tank(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Tank"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, unique_id(site.entry_id, "follow_presence", sub.subentry_id)
    )
    assert entity_id is not None
    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is not None, "off by default (D8 §5.5)"
    assert entry.entity_category is not None


async def test_the_knob_it_drives_reaches_the_next_tick(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """`Runtime.async_set_load_param("follow_presence", …)` is what the switch calls (INV-47).

    Exercised directly rather than by re-enabling the disabled entity and
    driving it through `switch.turn_off` - the same round trip `LoadForceSwitch`
    already proves for its own knob, and `_TARGET_KEYS` (`core/engine.py`)
    covers the rebuild this feeds into.
    """
    result = await _add_tank(hass, site, charger)
    result = await _answer(hass, result, **{**result["data_schema"]({}), "name": "Tank"})
    await hass.async_block_till_done()
    sub = next(s for s in site.subentries.values() if s.subentry_type == SUBENTRY_LOAD)
    runtime: Runtime = site.runtime_data
    assert bool(runtime.load_param(sub.subentry_id, "follow_presence")) is True

    await runtime.async_set_load_param(sub.subentry_id, "follow_presence", False)

    assert runtime.load_param(sub.subentry_id, "follow_presence") is False
