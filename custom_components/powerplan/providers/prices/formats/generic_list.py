"""The last row of D1 §2's table: a list of slots on an attribute (`generic_list`).

The escape hatch. Any entity whose attribute holds a list of slot dicts can be a
price source once the user says which attribute it is, which keys hold the start,
the end and the value, and what unit and currency those values are in. It is the
row that makes an integration powerplan has never heard of usable without a new
adapter - the one thing a format table cannot otherwise promise.

`end_key` may be left empty for a source that publishes only starts; the slot
then ends where the next one begins (INV-7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

from .base import ParsedPrices
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State


@register
@dataclass(frozen=True, slots=True)
class GenericList:
    """Reads a user-described list of `{start, end, value}` dicts (D1 §2)."""

    key: ClassVar[str] = "generic_list"
    platform: ClassVar[str | None] = None
    schema: ClassVar[Schema] = (
        Field(key="attribute", kind=FieldKind.TEXT, default="prices", required=True),
        Field(key="start_key", kind=FieldKind.TEXT, default="start", required=True),
        Field(key="end_key", kind=FieldKind.TEXT, default="end"),
        Field(key="value_key", kind=FieldKind.TEXT, default="value", required=True),
        Field(key="currency", kind=FieldKind.TEXT, required=True),
        Field(
            key="energy_unit",
            kind=FieldKind.SELECT,
            default=EnergyUnit.KWH.value,
            options=tuple(unit.value for unit in EnergyUnit),
            required=True,
        ),
        Field(
            key="magnitude",
            kind=FieldKind.SELECT,
            default=Magnitude.MAJOR.value,
            options=tuple(magnitude.value for magnitude in Magnitude),
            required=True,
        ),
    )

    currency: str
    attribute: str = "prices"
    start_key: str = "start"
    end_key: str = "end"
    value_key: str = "value"
    energy_unit: EnergyUnit = field(default=EnergyUnit.KWH)
    magnitude: Magnitude = field(default=Magnitude.MAJOR)

    def parse(self, state: State) -> ParsedPrices:
        """Return the intervals on the configured attribute."""
        rows = state.attributes.get(self.attribute)
        if rows is None:
            raise SourceParseError(
                f"{state.entity_id} has no attribute {self.attribute!r}; "
                f"it has {sorted(state.attributes)}"
            )
        if not isinstance(rows, list | tuple):
            raise SourceParseError(
                f"{state.entity_id}.{self.attribute} is a {type(rows).__name__}, not a list"
            )

        return ParsedPrices(
            intervals=tuple(self._interval(state, row) for row in rows),
            currency=self.currency,
            energy=EnergyUnit(self.energy_unit),
            magnitude=Magnitude(self.magnitude),
        )

    def _interval(self, state: State, row: Any) -> Interval:
        """Read one row of the list through the configured keys."""
        if not isinstance(row, dict):
            raise SourceParseError(
                f"{state.entity_id}.{self.attribute} holds a {type(row).__name__}, not a slot"
            )
        raw_end = row.get(self.end_key) if self.end_key else None
        return Interval(
            start=self._moment(state, row.get(self.start_key), self.start_key),
            end=self._moment(state, raw_end, self.end_key) if raw_end is not None else None,
            value=self._value(state, row.get(self.value_key)),
        )

    def _moment(self, state: State, raw: Any, key: str) -> datetime:
        """Parse a boundary, accepting a `datetime` or an ISO-8601 string."""
        if isinstance(raw, datetime):
            return raw
        parsed = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
        if parsed is None:
            raise SourceParseError(
                f"{state.entity_id}.{self.attribute}[…][{key!r}] is {raw!r}, not a timestamp"
            )
        return parsed

    def _value(self, state: State, raw: Any) -> Decimal:
        """Convert a price to `Decimal` through `str`, never through binary float."""
        if raw is None:
            raise SourceParseError(
                f"{state.entity_id}.{self.attribute}[…][{self.value_key!r}] is missing"
            )
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError) as err:
            raise SourceParseError(
                f"{state.entity_id}.{self.attribute}[…][{self.value_key!r}] is {raw!r}, not a price"
            ) from err


__all__ = ["GenericList"]
