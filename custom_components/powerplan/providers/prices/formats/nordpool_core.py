"""The core Nord Pool integration's current-price sensor (D1 §2, row 2).

`homeassistant/components/nordpool` publishes no forecast attributes at all: its
sensors are `current_price`, `next_price`, the day's highest and lowest, and the
currency - `tests/fixtures/captured/nordpool_core_no3.json` is the reference
house's own entity list and there is nothing else on it. Tomorrow's curve is only
reachable through the integration's response action, which is why D1 §2's row for
this platform says "use the `nordpool_action` source instead".

The row exists anyway, because the config flow detects a *platform* and has to be
able to say something about the sensor the user points at: it reads
`current_price` - the state - as the one interval it is the price of, and the
forecaster fills the rest (INV-5). A site configured this way and nothing else
plans on one known slot; the prices step offers `nordpool_action` first for
exactly that reason (D1 §6).

Slot length is configuration, not a guess: Nord Pool's MTU is 15 minutes, but an
hourly area is still an hour, and the sensor says
neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import SourceParseError

from .base import (
    FormatKind,
    ParsedPrices,
    declared_unit,
    slot_minutes_field,
    state_interval,
)
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State


@register
@dataclass(frozen=True, slots=True)
class NordpoolCore:
    """Reads the core integration's current price, and nothing else (D1 §2)."""

    key: ClassVar[str] = "nordpool_core"
    platform: ClassVar[str | None] = "nordpool"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = (
        Field(key="currency", kind=FieldKind.TEXT, default=""),
        slot_minutes_field(15),
    )

    currency: str = ""
    slot_minutes: int | str = 15

    def parse(self, state: State) -> ParsedPrices:
        """Return the one interval the sensor's state is the price of."""
        declared = declared_unit(state)
        currency, energy = declared if declared else (self.currency, EnergyUnit.KWH)
        if not currency:
            raise SourceParseError(
                f"{state.entity_id} declares no currency in its unit "
                f"({state.attributes.get('unit_of_measurement')!r}); set one on the source"
            )

        return ParsedPrices(
            intervals=(state_interval(state, slot_minutes=int(self.slot_minutes)),),
            currency=currency,
            energy=energy,
            magnitude=Magnitude.MAJOR,
        )


__all__ = ["NordpoolCore"]
