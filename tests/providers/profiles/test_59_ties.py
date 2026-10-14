"""Ties are data, not a DEBUG line (D4 §9 59, D-0747, D-0748).

Two candidates for a role bind neither, and the match now says which two, ranked
for the household: the device's own entity before a helper, a plausible reading
before an implausible one. A ranking never binds. A thermostat regulating on an
external sensor with a floor limit has its floor sensor asked, since its own
reading is the air.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.providers.profiles import DeviceView, registry
from custom_components.powerplan.providers.profiles.base import EntityView, MatchResult
from custom_components.powerplan.providers.profiles.candidates import rank

CLIMATE = "climate.bath"
POWER = {"device_class": "power", "unit_of_measurement": "W"}
TEMPERATURE = {"device_class": "temperature", "unit_of_measurement": "°C"}
SENSOR_MODE = [
    "F-Mode, floor sensor mode",
    "A2-mode, external room sensor mode",
    "A2F-mode, external sensor with floor limitation",
]


def view(*entities: EntityView, dead: frozenset[str] = frozenset()) -> DeviceView:
    """Return a thermostat device with `entities` beside its climate entity."""
    climate = EntityView(
        CLIMATE,
        "heat",
        {"temperature": 22.0, "current_temperature": 22.9, "hvac_modes": ["off", "heat"]},
    )
    return DeviceView(name="Bath", entities=(climate, *entities), dead=dead)


def sensor(entity_id: str, state: str, attributes: dict[str, Any], **kw: Any) -> EntityView:
    """Return one sensor."""
    return EntityView(entity_id, state, attributes, **kw)


def best(device: DeviceView) -> MatchResult:
    """Return the best match."""
    found = registry.best(device)
    assert found is not None
    return found


@pytest.mark.inv("INV-76")
def test_two_power_sensors_are_a_ranked_tie_and_bind_neither() -> None:
    """The device's own meter is offered before the household's template."""
    match = best(
        view(
            sensor("sensor.bath_power_template", "471", POWER, platform="template"),
            sensor("sensor.bath_power", "470", POWER, platform="zwave_js"),
        )
    )

    assert Role.POWER not in {binding.role for binding in match.bindings}
    assert match.ties[Role.POWER] == ("sensor.bath_power", "sensor.bath_power_template")


def test_an_implausible_reading_ranks_last_but_is_still_offered() -> None:
    """0.0 °C exactly is an unconnected input; it is shown, after the 22.9 °C one."""
    zero = sensor("sensor.a_air", "0.0", TEMPERATURE)
    live = sensor("sensor.b_air", "22.9", TEMPERATURE)

    assert [entity.entity_id for entity in rank([zero, live])] == ["sensor.b_air", "sensor.a_air"]


def test_a_floor_limited_external_mode_asks_for_the_floor_sensor() -> None:
    """A2F: the climate reads the air, so the floor sensor is a tie, not the climate."""
    mode = sensor(
        "select.bath_sensor_mode",
        SENSOR_MODE[2],
        {"options": SENSOR_MODE},
    )
    match = best(
        view(
            mode,
            sensor("sensor.bath_air_temperature", "0.0", TEMPERATURE),
            sensor("sensor.bath_air_temperature_3", "24.1", TEMPERATURE),
        )
    )

    floor = [b for b in match.bindings if b.role is Role.TEMP_FLOOR]
    assert floor == []
    assert match.ties[Role.TEMP_FLOOR] == (
        "sensor.bath_air_temperature_3",
        "sensor.bath_air_temperature",
    )


def test_in_floor_mode_the_thermostats_own_reading_is_the_floor() -> None:
    """F-mode: no question; the climate's `current_temperature` is the slab (D-0188)."""
    mode = sensor("select.bath_sensor_mode", SENSOR_MODE[0], {"options": SENSOR_MODE})
    match = best(
        view(
            mode,
            sensor("sensor.bath_air_temperature", "0.0", TEMPERATURE),
            sensor("sensor.bath_air_temperature_3", "22.9", TEMPERATURE),
        )
    )

    floor = next(b for b in match.bindings if b.role is Role.TEMP_FLOOR)
    assert (floor.entity_id, floor.attribute) == (CLIMATE, "current_temperature")
    assert Role.TEMP_FLOOR not in match.ties


def test_a_dead_sensor_leaves_the_pool_and_with_it_the_tie() -> None:
    """The recorder says one input never moved: the other binds alone."""
    match = best(
        view(
            sensor("sensor.bath_power_old", "0", POWER),
            sensor("sensor.bath_power", "470", POWER),
            dead=frozenset({"sensor.bath_power_old"}),
        )
    )

    assert next(b for b in match.bindings if b.role is Role.POWER).entity_id == "sensor.bath_power"
    assert Role.POWER not in match.ties


def test_each_profile_reports_only_its_own_ties() -> None:
    """The view's log is cleared between profiles, and after the match."""
    device = view(
        sensor("sensor.bath_power_a", "1", POWER),
        sensor("sensor.bath_power_b", "2", POWER),
    )
    matches = registry.match(device)

    assert all(set(m.ties) <= {Role.POWER, Role.ENERGY} for m in matches)
    assert device.ties == {}
