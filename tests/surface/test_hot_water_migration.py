"""D5 §9 38 - config entry minor version 8: a tank ready to be measured (D-0741, D-0743).

The reference tank's subentry: `deadline_fill` with `min_block_min` 30, a standby
answer at the old 60 W default, and its centre probe as the temperature sensor on
an ESPHome board that also reports `top` and `lower`. After minor 8 it keeps
`deadline_fill`, carries the reserve `hot_water` would keep, has the standby
answer cleared for the next setup to measure, and has the other two probes bound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan import async_migrate_entry
from custom_components.powerplan.const import DOMAIN, SUBENTRY_LOAD
from custom_components.powerplan.storage import (
    ENTRY_MINOR,
    ENTRY_MINOR_HOT_WATER,
    migrate_hot_water,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _tank(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": "water_heater",
        "strategy": "deadline_fill",
        "strategy_params": {"min_block_min": 30.0},
        "answers": {"standby_loss_w": 60.0, "temp_entity": None},
        "params": {"shiftable_kwh": 10.68, "standby_loss_w": 60.0, "litres": 300.0},
        "manual_overrides": ["comfort_c"],
    }
    data.update(overrides)
    return data


@pytest.mark.inv("INV-66")
def test_38_the_reference_tank_keeps_its_plan_and_is_ready_to_be_measured() -> None:
    """`deadline_fill` kept, 4.4 kWh written in for `hot_water`, the 60 W default cleared."""
    migrated = migrate_hot_water(_tank())
    assert migrated is not None
    assert migrated["strategy"] == "deadline_fill"
    assert migrated["strategy_params"] == {"min_block_min": 30.0, "reserve_kwh": 4.4}
    assert migrated["answers"]["standby_loss_w"] is None
    assert migrated["params"]["standby_loss_w"] == 60.0, "the running value stands until setup"


def test_38b_another_strategy_and_a_typed_standby_are_the_household_s() -> None:
    """A tank on `heat_capacitor` keeps it; a typed 80 W stays; other types are left."""
    kept = migrate_hot_water(
        _tank(
            strategy="heat_capacitor",
            strategy_params={"reserve_kwh": 3.0},
            answers={"standby_loss_w": 80.0},
        )
    )
    assert kept is None
    typed = _tank(manual_overrides=["standby_loss_w"])
    migrated = migrate_hot_water(typed)
    assert migrated is not None
    assert migrated["answers"]["standby_loss_w"] == 60.0
    assert migrate_hot_water({"type": "ev", "strategy": "deadline_fill"}) is None


async def test_38c_the_entry_migrates_and_finds_the_probes(hass: HomeAssistant) -> None:
    """At start from minor 7: the board's top and lower probes bound, the strategy kept."""
    board = MockConfigEntry(domain="esphome")
    board.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=board.entry_id, identifiers={("esphome", "vvb")}, name="Varmtvannsbereder"
    )
    registry = er.async_get(hass)
    probes = {
        name: registry.async_get_or_create(
            "sensor",
            "esphome",
            f"vvb_{name}",
            suggested_object_id=f"varmtvannsbereder_{name}_temperature",
            device_id=device.id,
            original_device_class="temperature",
        )
        for name in ("top", "center", "lower")
    }
    tank = _tank(answers={"standby_loss_w": 60.0, "temp_entity": probes["center"].id})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Site",
        data={},
        version=1,
        minor_version=7,
        subentries_data=[
            ConfigSubentryData(data=tank, subentry_type=SUBENTRY_LOAD, title="Tank", unique_id=None)
        ],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == ENTRY_MINOR_HOT_WATER == ENTRY_MINOR
    (sub,) = entry.subentries.values()
    assert sub.data["strategy"] == "deadline_fill"
    assert sub.data["answers"]["temp_top_entity"] == probes["top"].id
    assert sub.data["answers"]["temp_bottom_entity"] == probes["lower"].id
    assert sub.data["params"]["temp_top_entity"] == probes["top"].id


@pytest.mark.inv("INV-54")
def test_38d_the_old_65_c_default_becomes_the_guidance_s_70() -> None:
    """65 °C untyped → 70 °C, the cycle driven at 75; a typed 65 °C stays (D-0744)."""
    old = _tank(
        answers={"standby_loss_w": 60.0, "legionella_temp_c": 65.0},
        params={
            "shiftable_kwh": 10.68,
            "legionella_temp_c": 65.0,
            "legionella_drive_c": 75.0,
            "ready_temp_c": 75.0,
            "max_c": 80.0,
        },
    )
    migrated = migrate_hot_water(old)
    assert migrated is not None
    assert migrated["answers"]["legionella_temp_c"] == 70.0
    assert migrated["params"]["legionella_temp_c"] == 70.0
    assert migrated["params"]["legionella_drive_c"] == 75.0
    typed = migrate_hot_water({**old, "manual_overrides": ["legionella_temp_c"]})
    assert typed is not None
    assert typed["params"]["legionella_temp_c"] == 65.0


def test_38e_a_new_tank_is_given_70_c() -> None:
    """The questionnaire's default is the guidance's 70 °C, and it is still a setting."""
    from custom_components.powerplan.core.loads.questionnaire import QCtx  # noqa: PLC0415
    from custom_components.powerplan.core.loads.types import base as device_types  # noqa: PLC0415

    tank = device_types.get("water_heater")
    derived = tank.derive(tank.questionnaire.validate({}, QCtx()), QCtx())
    assert derived.params["legionella_temp_c"] == 70.0
    assert derived.params["legionella_drive_c"] == 75.0
    lower = tank.derive(tank.questionnaire.validate({"legionella_temp_c": 62.0}, QCtx()), QCtx())
    assert lower.params["legionella_temp_c"] == 62.0
