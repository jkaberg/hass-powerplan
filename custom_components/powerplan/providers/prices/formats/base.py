"""The `EntityFormat` protocol: one adapter per row of D1 §2's format table.

An adapter's whole job is to say where the prices are in one entity's state and
what unit they are in. It does not normalise, does not know the site's currency
and does not know what day is being asked for: `providers/prices/base.normalise`
and `EntitySource` do that once, for every row.

An adapter reads the state it is handed and nothing else. A payload it no longer
recognises is a `SourceParseError` - never a guess, never a silent empty list:
prices are what every decision downstream is made of, and a source that failed
must fail loudly enough for the forecaster to take over (INV-5) and for the
repair issue to name it (D1 §8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval

if TYPE_CHECKING:
    from homeassistant.core import State

    from custom_components.powerplan.core.pricing import Schema


@dataclass(frozen=True, slots=True)
class ParsedPrices:
    """What one adapter found in one entity state (D1 §2).

    The unit travels with the intervals because several integrations put it in an
    attribute the user can change (the HACS Nord Pool sensor's `price_type`), so
    it is a property of the reading, not of the configuration.
    """

    intervals: tuple[Interval, ...]
    currency: str
    energy: EnergyUnit
    magnitude: Magnitude


@runtime_checkable
class EntityFormat(Protocol):
    """How to read prices out of one integration's price entity (D1 §2).

    `platform` is the entity registry platform the config flow detects the format
    from; `None` means the adapter fits any entity and has to be chosen by hand
    (`generic_list`).
    """

    key: ClassVar[str]
    platform: ClassVar[str | None]
    schema: ClassVar[Schema]

    def parse(self, state: State) -> ParsedPrices:
        """Return the prices in `state`. Raises `SourceParseError`."""
        ...


__all__ = ["EntityFormat", "ParsedPrices"]
