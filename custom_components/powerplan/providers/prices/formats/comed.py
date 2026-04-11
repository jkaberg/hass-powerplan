"""ComEd hourly pricing: the current hour's average, in cents (D1 §2).

`homeassistant/components/comed_hourly_pricing` publishes two sensors - the live
five-minute price and the current hour's average - in ¢/kWh, and no forecast of
any kind. Illinois's hourly-pricing programme settles on the hour average, so
that is the sensor this row reads, and D1 §2's own note applies: "current 5-min
and hour averages only → forecaster must fill".

Two consequences, both deliberate. The price is the *state*, so the slot is the
one the entity was last written in (`state_interval`), and the unit is cents, so
the magnitude is MINOR and normalisation divides by 100 - a US site planning on
34 dollars per kWh instead of 34 cents would shed every load in the house.
`¢/kWh` names no ISO currency, so the currency is configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import FormatKind, ParsedPrices, slot_minutes_field, state_interval
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State


@register
@dataclass(frozen=True, slots=True)
class Comed:
    """Reads the ComEd hourly-pricing sensor's state (D1 §2)."""

    key: ClassVar[str] = "comed"
    platform: ClassVar[str | None] = "comed_hourly_pricing"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = (
        Field(key="currency", kind=FieldKind.TEXT, default="USD", required=True),
        slot_minutes_field(60),
    )

    currency: str = "USD"
    slot_minutes: int | str = 60

    def parse(self, state: State) -> ParsedPrices:
        """Return the hour the sensor's state is the average price of."""
        return ParsedPrices(
            intervals=(state_interval(state, slot_minutes=int(self.slot_minutes)),),
            currency=self.currency,
            energy=EnergyUnit.KWH,
            magnitude=Magnitude.MINOR,
        )


__all__ = ["Comed"]
