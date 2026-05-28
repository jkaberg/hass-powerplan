"""`circuit` reads a sub-metered circuit's power entity (D3 §9, provider line).

The exit line's last clause - "circuit sum with settling" - has two halves. The
sub-meter half is here: one power entity in W or kW into a `MeterSample`,
`unavailable` degrading to `Quality.UNAVAILABLE`, and the entity the runtime
subscribes to. The **sum** half is the core's (D6 §5.8), asserted in
`tests/core/allocation/test_14_circuits.py` on the members' `ControlledView`s
with D3 §5.8's settling rule - a circuit without a sub-meter has no provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.metering import Quality
from custom_components.powerplan.providers.meters import CircuitMeter, MeterSource

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

GARAGE_POWER = "sensor.garage_feed_power"


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [("6900", "W", 6900.0), ("6.9", "kW", 6900.0)],
    ids=["w", "kw"],
)
async def test_the_circuit_power_is_scaled_to_watts(
    hass: HomeAssistant, state: str, unit: str, expected: float
) -> None:
    """The garage clamp in W or kW lands in watts, and nothing else is read (D3 §2)."""
    hass.states.async_set(
        GARAGE_POWER, state, {"device_class": "power", "unit_of_measurement": unit}
    )

    sample = await CircuitMeter(hass, GARAGE_POWER).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.value == pytest.approx(expected)
    assert sample.grid_w.quality is Quality.OK
    assert sample.grid_w.source == GARAGE_POWER
    assert sample.import_kwh is None
    assert sample.phase_a is None


@pytest.mark.inv("INV-53")
@pytest.mark.parametrize("state", ["unavailable", "unknown", "not a number"])
async def test_a_sub_meter_that_cannot_answer_is_unavailable(
    hass: HomeAssistant, state: str
) -> None:
    """Blindness is explicit: the circuit falls back to its members' sum (D6 §8)."""
    hass.states.async_set(GARAGE_POWER, state, {"unit_of_measurement": "W"})

    sample = await CircuitMeter(hass, GARAGE_POWER).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.quality is Quality.UNAVAILABLE


@pytest.mark.inv("INV-53")
async def test_a_sub_meter_entity_that_does_not_exist_is_unavailable(hass: HomeAssistant) -> None:
    """A deleted clamp degrades; it does not raise."""
    sample = await CircuitMeter(hass, GARAGE_POWER).sample(dt_util.utcnow())

    assert sample.grid_w is not None
    assert sample.grid_w.quality is Quality.UNAVAILABLE


def test_the_provider_is_a_meter_source_and_names_its_entity(hass: HomeAssistant) -> None:
    """A plain second `MeterSource` (D3 §2): the runtime subscribes to its one entity."""
    source = CircuitMeter(hass, GARAGE_POWER)

    assert isinstance(source, MeterSource)
    assert source.key == "circuit"
    assert source.entity_ids() == frozenset({GARAGE_POWER})
