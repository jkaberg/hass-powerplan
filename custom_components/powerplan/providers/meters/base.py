"""The `MeterSource` protocol and the entity→`Reading` adapters (D3 §3, §5.1).

Scaling happens here and nowhere else. The core is handed watts, kWh and amps
, signed import + / export − (INV-19), and every reading
carries the instant Home Assistant received it - `State.last_reported`, never a
timestamp the device claimed (D3 §8, "clock skew between meter and HA").

Three ways a bound entity can fail to answer, and all three degrade to
`Quality.UNAVAILABLE` rather than raising: the entity is gone, its state is
`unavailable` / `unknown` / unparseable, or its unit is not one this quantity
knows. Blindness never opens a gate (INV-17), and an unavailable bound entity is
explicit, not silent (INV-53). A role that was never configured is `None`
instead - a distinction the core relies on, because "no export register" and
"the export register is broken" are different facts.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, runtime_checkable

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.metering import MeterSample, Quality, Reading

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

#: A unit the entity may declare → the factor to this domain's own unit.
type UnitTable = Mapping[str, float]

#: Power in watts. D3 §6 rejects a power entity whose unit is neither W nor kW.
POWER_W: Final[UnitTable] = {
    UnitOfPower.WATT: 1.0,
    UnitOfPower.KILO_WATT: 1000.0,
}
#: Cumulative energy in kWh.
ENERGY_KWH: Final[UnitTable] = {
    UnitOfEnergy.KILO_WATT_HOUR: 1.0,
    UnitOfEnergy.WATT_HOUR: 0.001,
}
#: Per-phase current in amps. Per-phase limits are amps, never watts.
CURRENT_A: Final[UnitTable] = {
    UnitOfElectricCurrent.AMPERE: 1.0,
}

#: The states that mean "I cannot answer" (INV-53).
_BLIND: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN, ""})

#: Added before truncating, so `last_reset` rounds to the nearest second (D-0083).
_HALF_SECOND: Final = timedelta(microseconds=500_000)


@runtime_checkable
class MeterSource(Protocol):
    """Where the site's electrical readings come from (D3 §4, HLD §6.3).

    One call per tick yields the whole bundle: `MeterSample` is what D3 §4 says
    a source produces, and `WindowMeter.sample` takes it as one frozen object,
    so a source cannot hand out readings taken at two different instants.

    `sample` is async because a v1.x source is a subscription or an HTTP client
    (`dsmr`, `tibber_pulse`, D3 §3), not because reading `hass.states` blocks.
    """

    key: ClassVar[str]

    async def sample(self, now: datetime) -> MeterSample:
        """Return everything the meter knows at `now`."""
        ...

    def entity_ids(self) -> frozenset[str]:
        """Return every entity this source reads, for the runtime to subscribe to.

        The source registers nothing itself: `async_track_state_change_event` is
        called once by `runtime.py`, which owns the subscription's lifetime
        (`entry.async_on_unload`, D7 §5.3).
        """
        ...


class EntityReader:
    """Reads one Home Assistant entity as one physical quantity (D3 §3).

    A fault is logged once per entity and reason: a unit that changed under an
    integration update, or a state that is not a number, is a thing the user has
    to fix, while `unavailable` is ordinary and stays at DEBUG (D3 §8 - per
    sample at DEBUG, transitions at WARNING).
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind the reader to `hass`."""
        self._hass = hass
        self._faulted: set[tuple[str, str]] = set()

    def power_w(self, entity_id: str | None, now: datetime) -> Reading | None:
        """Read `entity_id` as signed watts, or `None` if the role is unset."""
        return self._read(entity_id, now, POWER_W, "power")

    def energy_kwh(self, entity_id: str | None, now: datetime) -> Reading | None:
        """Read `entity_id` as cumulative kWh, or `None` if the role is unset."""
        return self._read(entity_id, now, ENERGY_KWH, "energy")

    def current_a(self, entity_id: str | None, now: datetime) -> Reading | None:
        """Read `entity_id` as amps, or `None` if the role is unset."""
        return self._read(entity_id, now, CURRENT_A, "current")

    def last_reset(self, entity_id: str | None) -> datetime | None:
        """Return the `last_reset` of a `total` accumulator, to the second (D3 §4).

        A meter that computes its own window value says where that window began
        in `last_reset`; `WindowMeter` compares it to the window start for
        equality, so the sub-millisecond jitter Home Assistant records
        (`13:00:00.001962`) is rounded away (`design/DECISIONS.md` D-0083).
        """
        if entity_id is None:
            return None
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        raw = state.attributes.get("last_reset")
        parsed = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
        if parsed is None:
            return None
        return dt_util.as_utc((parsed + _HALF_SECOND).replace(microsecond=0))

    def _read(
        self, entity_id: str | None, now: datetime, units: UnitTable, quantity: str
    ) -> Reading | None:
        """Turn one entity state into a `Reading`, degrading rather than raising."""
        if entity_id is None:
            return None

        state = self._hass.states.get(entity_id)
        if state is None:
            _LOGGER.debug("%s entity %s does not exist", quantity, entity_id)
            return Reading(value=0.0, at=now, source=entity_id, quality=Quality.UNAVAILABLE)

        at = state.last_reported
        if state.state in _BLIND:
            _LOGGER.debug("%s entity %s is %s", quantity, entity_id, state.state)
            return Reading(value=0.0, at=at, source=entity_id, quality=Quality.UNAVAILABLE)

        try:
            value = float(state.state)
        except ValueError:
            self._fault(entity_id, f"{state.state!r} is not a number")
            return Reading(value=0.0, at=at, source=entity_id, quality=Quality.UNAVAILABLE)

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        factor = units.get(unit) if isinstance(unit, str) else None
        if factor is None:
            self._fault(entity_id, f"{unit!r} is not a {quantity} unit powerplan reads")
            return Reading(value=0.0, at=at, source=entity_id, quality=Quality.UNAVAILABLE)

        return Reading(value=value * factor, at=at, source=entity_id, quality=Quality.OK)

    def _fault(self, entity_id: str, reason: str) -> None:
        """Log a configuration fault once per entity and reason (D3 §8)."""
        key = (entity_id, reason)
        if key in self._faulted:
            return
        self._faulted.add(key)
        _LOGGER.warning(
            "meter entity %s cannot be read (%s); powerplan is blind on this role "
            "until it is fixed",
            entity_id,
            reason,
        )


__all__ = [
    "CURRENT_A",
    "ENERGY_KWH",
    "POWER_W",
    "EntityReader",
    "MeterSource",
    "UnitTable",
]
