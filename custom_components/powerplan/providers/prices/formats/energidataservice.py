"""Energi Data Service: `raw_today` / `raw_tomorrow` of `{hour, price}` (D1 §2).

`MTrab/energidataservice` is what a Danish household runs. Its sensor's
`_add_raw()` builds each entry as `{"hour": <datetime>, "price": round(price,
decimals)}` - a start and nothing else, so the slot ends where the next one
begins (INV-7) - and `_get_current_price()` publishes three attributes this
adapter needs beside them: `unit` (the configured price type, kWh or MWh),
`currency`, and `use_cent`.

All three are read on every parse rather than stored, because all three are the
user's own configuration in *that* integration and can change without powerplan
being told (`design/DECISIONS.md` D-0085). `use_cent` is the one that bites: øre per
kWh and kroner per kWh differ by a factor of a hundred, and a planner that got it
wrong would treat every hour as equally unaffordable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final

from custom_components.powerplan.core.pricing import Schema
from custom_components.powerplan.core.pricing.normalise import Magnitude
from custom_components.powerplan.providers.prices.base import SourceParseError

from .base import ENERGY_UNITS, FormatKind, ParsedPrices, attribute_intervals
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: The two attributes that carry the forecast, in the order they are read.
RAW_ATTRIBUTES: Final = ("raw_today", "raw_tomorrow")


@register
@dataclass(frozen=True, slots=True)
class EnergiDataService:
    """Reads the Energi Data Service sensor's raw attribute lists (D1 §2)."""

    key: ClassVar[str] = "energidataservice"
    platform: ClassVar[str | None] = "energidataservice"
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = ()

    def parse(self, state: State) -> ParsedPrices:
        """Return every priced hour the sensor knows about."""
        currency = state.attributes.get("currency")
        if not isinstance(currency, str) or not currency:
            raise SourceParseError(
                f"{state.entity_id} has no `currency` attribute; "
                "this does not look like an Energi Data Service sensor"
            )

        raw_unit = state.attributes.get("unit")
        energy = ENERGY_UNITS.get(raw_unit.lower()) if isinstance(raw_unit, str) else None
        if energy is None:
            raise SourceParseError(
                f"{state.entity_id} prices per {raw_unit!r}; powerplan reads kWh and MWh"
            )

        return ParsedPrices(
            intervals=tuple(
                attribute_intervals(
                    state,
                    RAW_ATTRIBUTES,
                    start_key="hour",
                    value_key="price",
                    required=False,
                )
            ),
            currency=currency,
            energy=energy,
            magnitude=Magnitude.MINOR if state.attributes.get("use_cent") else Magnitude.MAJOR,
        )


__all__ = ["EnergiDataService"]
