"""A circuit's own meter: one power entity, read like the site's (D3 §2, §3).

A sub-metered circuit binds a plain second `MeterSource` - typically a clamp on
the garage feed - and only its power matters: circuit limits are instantaneous
(D6 §5.8), so no `WindowMeter` is built over it and no register is read. The
reading goes into `Inputs.circuits` under the circuit's key and D6's
`CircuitLimit` takes it in `prepare()`; `unavailable`, or older than the site
meter's stale cap, it is dropped and the circuit falls back to the sum of its
members' own measurements plus the unmetered allowance (D6 §8).

The **sum** itself is the core's: `CircuitLimit._measured_total` adds the
members' `ControlledView`s with D3 §5.8's settling rule, so a circuit without a
sub-meter needs no provider at all. This module is the sub-meter half.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from custom_components.powerplan.core.metering import MeterSample

from .base import EntityReader

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class CircuitMeter:
    """Reads one circuit's power entity into a `MeterSample` (D3 §3)."""

    key: ClassVar[str] = "circuit"

    def __init__(self, hass: HomeAssistant, power_entity: str) -> None:
        """Bind the source to `hass` and to the circuit's power entity."""
        self._power_entity = power_entity
        self._reader = EntityReader(hass)

    async def sample(self, now: datetime) -> MeterSample:
        """Read the circuit's power once, at `now`; W and kW both land in watts."""
        sample = MeterSample(grid_w=self._reader.power_w(self._power_entity, now))
        _LOGGER.debug("circuit sample %s: %s", self._power_entity, sample.grid_w)
        return sample

    def entity_ids(self) -> frozenset[str]:
        """Return the one entity the runtime subscribes to for this circuit (D7 §5.3)."""
        return frozenset({self._power_entity})


__all__ = ["CircuitMeter"]
