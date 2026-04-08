"""`EntitySource`: prices read off a price entity the user already has (D1 §3).

One class for every row of D1 §2's format table. The row-specific knowledge - which
attribute, which keys, which unit - is the `EntityFormat` adapter's; everything
else is the same for all thirteen rows and is here: read the state through
`hass.states.get`, normalise once (D1 §5.2), and hand back the slots of the local
day that was asked for.

`publication()` is `None`: an entity has no publication time. It updates when its
integration updates it, so the runtime re-reads it on the state change it is
already subscribed to, and again at the hole check (D1 §5.1). Nothing is
subscribed here - `entity_ids()` is what the runtime registers (INV-3).
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, ClassVar

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.powerplan.core.pricing import Carrier, Direction, Publication, RawSlot
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import SourceEmptyError, SourceUnavailableError, normalise

if TYPE_CHECKING:
    from datetime import date, tzinfo
    from decimal import Decimal

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing import Schema

    from .formats import EntityFormat

_LOGGER = logging.getLogger(__name__)


class EntitySource:
    """Reads a price entity's attributes through one format adapter (D1 §2, §3)."""

    key: ClassVar[str] = "entity"
    schema: ClassVar[Schema] = ()

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entity_id: str,
        adapter: EntityFormat,
        site_currency: str,
        tz: tzinfo,
        carrier: Carrier = Carrier.ELECTRICITY,
        direction: Direction = Direction.IMPORT,
        fx_rate: Decimal | None = None,
    ) -> None:
        """Bind the source to one entity, one adapter and the site's currency."""
        self._hass = hass
        self._entity_id = entity_id
        self._adapter = adapter
        self._site_currency = site_currency
        self._tz = tz
        self.carrier = carrier
        self.direction = direction
        self._fx_rate = fx_rate

    def publication(self) -> Publication | None:
        """Return `None`: an entity is continuous, it has no publication time."""
        return None

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return what the entity currently quotes in (D1 §4).

        Read from the live state, because several integrations let the user change
        it. With the entity unavailable there is nothing to read and the site's own
        currency, per kWh, in major units is the honest answer - the value it would
        have after normalisation anyway.
        """
        state = self._hass.states.get(self._entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return (self._site_currency, EnergyUnit.KWH, Magnitude.MAJOR)
        parsed = self._adapter.parse(state)
        return (parsed.currency, parsed.energy, parsed.magnitude)

    def entity_ids(self) -> frozenset[str]:
        """Return the one entity this source reads (D7 §5.3 subscribes to it)."""
        return frozenset({self._entity_id})

    async def fetch(self, day: date) -> list[RawSlot]:
        """Return the slots the entity knows for the local day `day` (D1 §5.2)."""
        state = self._hass.states.get(self._entity_id)
        if state is None:
            raise SourceUnavailableError(f"{self._entity_id} does not exist")
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            raise SourceUnavailableError(f"{self._entity_id} is {state.state}")

        parsed = self._adapter.parse(state)
        slots = normalise(
            parsed.intervals,
            source=self.key,
            currency=parsed.currency,
            site_currency=self._site_currency,
            energy=parsed.energy,
            magnitude=parsed.magnitude,
            source_tz=self._tz,
            fetched_at=state.last_updated,
            fx_rate=self._fx_rate,
        )

        start, end = self._local_day(day)
        wanted = [slot for slot in slots if slot.end > start and slot.start < end]
        if not wanted:
            raise SourceEmptyError(f"{self._entity_id} has no prices for {day}")
        _LOGGER.debug(
            "%s: %d of %d slots are within %s", self._entity_id, len(wanted), len(slots), day
        )
        return wanted

    def _local_day(self, day: date) -> tuple[datetime, datetime]:
        """Return the instants the local day `day` runs between (23, 24 or 25 h)."""
        return (
            datetime.combine(day, time.min, tzinfo=self._tz),
            datetime.combine(day + timedelta(days=1), time.min, tzinfo=self._tz),
        )


__all__ = ["EntitySource"]
