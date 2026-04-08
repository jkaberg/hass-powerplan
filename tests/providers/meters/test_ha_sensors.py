"""`ha_sensors` turns entity states into `Reading`s (D3 §9, provider line).

The exit line is: "`ha_sensors` maps W and kW; unavailable → `Quality.UNAVAILABLE`;
missing optional roles → `None`; circuit sum with settling."

The **circuit sum with settling is WP2.5** - `providers/meters/circuit.py` and
D6's `constraints/circuit.py` land together with the garage circuit, and there is
nothing here to sum yet. This module covers the other three clauses plus the two
the WP row adds: the register read off the captured AMS dump, and per-phase
currents.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.metering import Quality, age
from custom_components.powerplan.providers.meters import HaSensorsConfig, HaSensorsMeter

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

# The captured AMS dump (`tests/fixtures/captured/ams_datek_eva_han.json`).
AMS_POWER = "sensor.dataskap_strommaler_power"
AMS_REGISTER = "sensor.dataskap_strommaler_energy"
AMS_PRODUCED = "sensor.dataskap_strommaler_produced_energy"
AMS_HOUR = "sensor.akkumulert_stromforbruk_per_innevaerende_time"

POWER = "sensor.grid_power"
REGISTER = "sensor.grid_import"


def meter(hass: HomeAssistant, **config: Any) -> HaSensorsMeter:
    """Build the provider over `config`, defaulting the grid power role."""
    config.setdefault("grid_power", POWER)
    return HaSensorsMeter(hass, HaSensorsConfig(**config))


# --------------------------------------------------------------------------- #
# W and kW
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [
        ("1290", "W", 1290.0),
        ("-2400", "W", -2400.0),
        ("1.29", "kW", 1290.0),
        ("-2.4", "kW", -2400.0),
    ],
    ids=["w", "w_export", "kw", "kw_export"],
)
async def test_power_is_scaled_to_watts(
    hass: HomeAssistant, state: str, unit: str, expected: float
) -> None:
    """A power entity in W or kW becomes signed watts (D3 §5.1, INV-19)."""
    hass.states.async_set(POWER, state, {"device_class": "power", "unit_of_measurement": unit})

    sample = await meter(hass).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.value == pytest.approx(expected)
    assert sample.grid_w.quality is Quality.OK
    assert sample.grid_w.source == POWER


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [("174500.96", "kWh", 174500.96), ("174500960", "Wh", 174500.96)],
    ids=["kwh", "wh"],
)
async def test_register_is_scaled_to_kwh(
    hass: HomeAssistant, state: str, unit: str, expected: float
) -> None:
    """An energy register in kWh or Wh becomes kWh (D3 §5.1)."""
    hass.states.async_set(POWER, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(
        REGISTER,
        state,
        {"device_class": "energy", "state_class": "total_increasing", "unit_of_measurement": unit},
    )

    sample = await meter(hass, import_register=REGISTER).sample(dt_util.utcnow())

    assert sample.import_kwh is not None
    assert sample.import_kwh.value == pytest.approx(expected)
    assert sample.import_kwh.quality is Quality.OK


async def test_a_unit_we_do_not_know_degrades_rather_than_scales(hass: HomeAssistant) -> None:
    """An unexpected unit is blindness, never an unscaled number (D3 §6, §8)."""
    hass.states.async_set(POWER, "1.29", {"device_class": "power", "unit_of_measurement": "MW"})

    sample = await meter(hass).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.quality is Quality.UNAVAILABLE


# --------------------------------------------------------------------------- #
# unavailable → Quality.UNAVAILABLE  (INV-53)
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-53")
@pytest.mark.parametrize("state", ["unavailable", "unknown", "not a number", ""])
async def test_a_bound_entity_without_a_number_is_unavailable(
    hass: HomeAssistant, state: str
) -> None:
    """A bound entity that cannot answer degrades explicitly (INV-53, INV-17)."""
    hass.states.async_set(POWER, state, {"device_class": "power", "unit_of_measurement": "W"})

    sample = await meter(hass).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.quality is Quality.UNAVAILABLE


@pytest.mark.inv("INV-53")
async def test_a_bound_entity_that_does_not_exist_is_unavailable(hass: HomeAssistant) -> None:
    """A deleted entity degrades; it does not raise (INV-53)."""
    sample = await meter(hass, import_register=REGISTER).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.quality is Quality.UNAVAILABLE
    assert sample.import_kwh is not None
    assert sample.import_kwh.quality is Quality.UNAVAILABLE


# --------------------------------------------------------------------------- #
# missing optional roles → None
# --------------------------------------------------------------------------- #


async def test_roles_that_were_never_configured_are_none(hass: HomeAssistant) -> None:
    """An unconfigured role is `None`, which is not the same as unavailable."""
    hass.states.async_set(POWER, "1290", {"unit_of_measurement": "W"})

    sample = await meter(hass).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.import_kwh is None
    assert sample.export_kwh is None
    assert sample.production_w is None
    assert sample.meter_window_kwh is None
    assert sample.meter_window_start is None
    assert sample.phase_a is None
    assert sample.battery_charge_w is None


# --------------------------------------------------------------------------- #
# the captured reference house
# --------------------------------------------------------------------------- #


async def test_the_register_from_the_captured_ams_dump(
    hass: HomeAssistant,
    captured: Callable[[str], dict[str, Any]],
    restore_capture: Callable[[dict[str, Any]], None],
) -> None:
    """The real Datek EVA HAN meter reads through unchanged (D9 §5.8)."""
    restore_capture(captured("ams_datek_eva_han"))

    sample = await meter(
        hass,
        grid_power=AMS_POWER,
        import_register=AMS_REGISTER,
        export_register=AMS_PRODUCED,
    ).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.value == pytest.approx(1290.0)
    assert sample.import_kwh is not None
    assert sample.import_kwh.value == pytest.approx(174500.96)
    assert sample.import_kwh.quality is Quality.OK
    assert sample.export_kwh is not None
    assert sample.export_kwh.value == pytest.approx(0.0)


@pytest.mark.inv("INV-53")
async def test_the_captured_unavailable_phase_sensors_degrade(
    hass: HomeAssistant,
    captured: Callable[[str], dict[str, Any]],
    restore_capture: Callable[[dict[str, Any]], None],
) -> None:
    """Two of the house's phase sensors are `unavailable` (INV-53)."""
    restore_capture(captured("ams_datek_eva_han"))

    sample = await meter(
        hass,
        grid_power=AMS_POWER,
        phase_current=("sensor.strommaler_power_phase_b", "sensor.strommaler_power_phase_c", None),
    ).sample(dt_util.utcnow())

    assert sample.phase_a is not None
    assert [reading.quality for reading in sample.phase_a] == [
        Quality.UNAVAILABLE,
        Quality.UNAVAILABLE,
    ]


# --------------------------------------------------------------------------- #
# per-phase currents
# --------------------------------------------------------------------------- #


async def test_phase_currents_are_amps_in_l1_l2_l3_order(hass: HomeAssistant) -> None:
    """Three current entities become three readings in amps (D3 §6)."""
    hass.states.async_set(POWER, "1290", {"unit_of_measurement": "W"})
    for phase, amps in (("l1", "12.5"), ("l2", "3.0"), ("l3", "27.75")):
        hass.states.async_set(
            f"sensor.current_{phase}",
            amps,
            {"device_class": "current", "unit_of_measurement": "A"},
        )

    sample = await meter(
        hass,
        phase_current=("sensor.current_l1", "sensor.current_l2", "sensor.current_l3"),
    ).sample(dt_util.utcnow())

    assert sample.phase_a is not None
    assert [reading.value for reading in sample.phase_a] == pytest.approx([12.5, 3.0, 27.75])
    assert all(reading.quality is Quality.OK for reading in sample.phase_a)


async def test_a_single_phase_site_yields_one_current(hass: HomeAssistant) -> None:
    """One configured phase yields a one-element tuple, not three."""
    hass.states.async_set(POWER, "1290", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.current_l1", "9.5", {"unit_of_measurement": "A"})

    sample = await meter(hass, phase_current=("sensor.current_l1", None, None)).sample(
        dt_util.utcnow()
    )

    assert sample.phase_a is not None
    assert len(sample.phase_a) == 1
    assert sample.phase_a[0].value == pytest.approx(9.5)


# --------------------------------------------------------------------------- #
# the meter's own window value, and staleness
# --------------------------------------------------------------------------- #


async def test_the_meter_window_start_comes_from_last_reset(
    hass: HomeAssistant,
    captured: Callable[[str], dict[str, Any]],
    restore_capture: Callable[[dict[str, Any]], None],
) -> None:
    """A `total` accumulator announces its own window start (D3 §4, D-0083)."""
    restore_capture(captured("ams_datek_eva_han"))

    sample = await meter(hass, grid_power=AMS_POWER, meter_window=AMS_HOUR).sample(dt_util.utcnow())

    assert sample.meter_window_kwh is not None
    assert sample.meter_window_kwh.value == pytest.approx(0.5044303971813889)
    # `last_reset` is 13:00:00.001962Z; a window start sits on the boundary.
    assert sample.meter_window_start is not None
    assert sample.meter_window_start.isoformat() == "2025-09-20T13:00:00+00:00"


@pytest.mark.inv("INV-17")
async def test_a_reading_is_stamped_with_when_the_entity_last_reported(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """`Reading.at` is HA's receipt time, so the core can age it (D3 §8, INV-17)."""
    freezer.move_to("2025-09-20T13:25:11+00:00")
    hass.states.async_set(POWER, "1290", {"unit_of_measurement": "W"})

    freezer.tick(timedelta(seconds=45))
    now = dt_util.utcnow()
    sample = await meter(hass).sample(now)

    assert sample.grid_w is not None
    assert age(sample.grid_w, now) == pytest.approx(45.0)


# --------------------------------------------------------------------------- #
# what the runtime subscribes to
# --------------------------------------------------------------------------- #


async def test_entity_ids_is_every_bound_entity(hass: HomeAssistant) -> None:
    """The provider names its entities; the runtime registers the listener."""
    source = meter(
        hass,
        import_register=REGISTER,
        production_power="sensor.pv",
        phase_current=("sensor.current_l1", None, "sensor.current_l3"),
    )

    assert source.entity_ids() == frozenset(
        {POWER, REGISTER, "sensor.pv", "sensor.current_l1", "sensor.current_l3"}
    )
