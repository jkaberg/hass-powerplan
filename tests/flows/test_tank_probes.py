"""D4 §9 55 - a tank's other probes, suggested from the device its probe is on (D-0741).

The reference tank's ESPHome board reports `top`, `center` and `lower`
temperatures; the household names the centre one and the flow suggests the other
two by their names. A sensor whose name says neither, or one on another device,
is never bound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.flow.load import _EXTRA_ROLE_ANSWERS, sibling_probes

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _board(hass: HomeAssistant, names: tuple[str, ...]) -> dict[str, str]:
    """Register one device with a temperature sensor per name; return name → entity id."""
    entry = MockConfigEntry(domain="esphome")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("esphome", "vvb")}, name="Varmtvannsbereder"
    )
    registry = er.async_get(hass)
    out = {}
    for name in names:
        made = registry.async_get_or_create(
            "sensor",
            "esphome",
            f"vvb_{name}",
            suggested_object_id=f"varmtvannsbereder_{name}_temperature",
            device_id=device.id,
            original_device_class="temperature",
        )
        out[name] = made.entity_id
    return out


async def test_55e_the_top_and_lower_probes_are_suggested(hass: HomeAssistant) -> None:
    """Centre answered: top and lower suggested; the board's own air sensor is not."""
    board = _board(hass, ("top", "center", "lower", "internal"))
    found = sibling_probes(hass, "water_heater", {"temp_entity": board["center"]})
    assert found == {"temp_top_entity": board["top"], "temp_bottom_entity": board["lower"]}


async def test_55f_an_answer_stands_and_a_lone_probe_has_no_siblings(hass: HomeAssistant) -> None:
    """A top probe the household picked is kept; no probe answered, nothing suggested."""
    board = _board(hass, ("top", "center", "lower"))
    found = sibling_probes(
        hass, "water_heater", {"temp_entity": board["center"], "temp_top_entity": "sensor.mine"}
    )
    assert found == {"temp_bottom_entity": board["lower"]}
    assert sibling_probes(hass, "water_heater", {"temp_entity": None}) == {}
    assert sibling_probes(hass, "floor_heating", {"temp_entity": board["center"]}) == {}


def test_55g_the_tank_s_probe_answers_are_bound() -> None:
    """The probe answer was never bound before (D-0741); now all three are."""
    assert _EXTRA_ROLE_ANSWERS["water_heater"] == (
        ("temp_entity", Role.TEMP),
        ("temp_top_entity", Role.TEMP_TOP),
        ("temp_bottom_entity", Role.TEMP_BOTTOM),
    )
