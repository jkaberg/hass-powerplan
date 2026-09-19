"""The last row of D1 §2's table: a list of slots on an attribute (`generic_list`).

The escape hatch. Any entity whose attribute holds a list of slot dicts can be a
price source once the user says which attribute it is, which keys hold the start,
the end and the value, and what unit and currency those values are in. It is the
row that makes an integration powerplan has never heard of usable without a new
adapter - the one thing a format table cannot otherwise promise.

`end_key` may be left empty for a source that publishes only starts; the slot
then ends where the next one begins (INV-7).

The row also reaches three payloads a plain list would not: `value_key` may be
a dotted path (`price_tax_included.amount`), `scale` multiplies every price (a
payload in 1e-7 euro is `0.0000001`), and `tomorrow_attribute` names a second
list read after the first (D1 §2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

from .base import FormatKind, ParsedPrices, dotted, listed, moment, price
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State


@register
@dataclass(frozen=True, slots=True)
class GenericList:
    """Reads a user-described list of `{start, end, value}` dicts (D1 §2)."""

    key: ClassVar[str] = "generic_list"
    platform: ClassVar[str | None] = None
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = (
        Field(key="attribute", kind=FieldKind.TEXT, default="prices", required=True),
        Field(key="tomorrow_attribute", kind=FieldKind.TEXT, default=""),
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
        Field(key="scale", kind=FieldKind.NUMBER, default="1", required=True),
    )

    currency: str
    attribute: str = "prices"
    start_key: str = "start"
    end_key: str = "end"
    value_key: str = "value"
    energy_unit: EnergyUnit = field(default=EnergyUnit.KWH)
    magnitude: Magnitude = field(default=Magnitude.MAJOR)
    tomorrow_attribute: str = ""
    scale: Decimal = Decimal(1)

    def parse(self, state: State) -> ParsedPrices:
        """Return the intervals on the configured attribute, then on tomorrow's."""
        rows = state.attributes.get(self.attribute)
        if rows is None:
            raise SourceParseError(
                f"{state.entity_id} has no attribute {self.attribute!r}; "
                f"it has {sorted(state.attributes)}"
            )
        intervals = self._intervals(state, self.attribute, rows)
        tomorrow = (
            state.attributes.get(self.tomorrow_attribute) if self.tomorrow_attribute else None
        )
        if tomorrow is not None:
            # Absent before publication: nothing known yet, not a failure.
            intervals += self._intervals(state, self.tomorrow_attribute, tomorrow)
        return ParsedPrices(
            intervals=tuple(intervals),
            currency=self.currency,
            energy=EnergyUnit(self.energy_unit),
            magnitude=Magnitude(self.magnitude),
        )

    def _intervals(self, state: State, attribute: str, rows: Any) -> list[Interval]:
        """Read one list-valued attribute through the configured keys."""
        where = f"{state.entity_id}.{attribute}"
        return [
            self._interval(row, where=f"{where}[{index}]")
            for index, row in enumerate(listed(rows, where=where))
        ]

    def _interval(self, row: Any, *, where: str) -> Interval:
        """Read one row of the list through the configured keys.

        Unlike the integration-specific rows, a missing price here is a *missing*
        price: the user named the key, so a row without it is a misconfiguration
        to be reported, not an interval the source could not price.
        """
        if not isinstance(row, dict):
            raise SourceParseError(f"{where} is a {type(row).__name__}, not a slot")
        raw_value = dotted(row, self.value_key)
        if raw_value is None:
            raise SourceParseError(f"{where}[{self.value_key!r}] is missing")
        raw_end = row.get(self.end_key) if self.end_key else None
        return Interval(
            start=moment(row.get(self.start_key), where=f"{where}[{self.start_key!r}]"),
            end=(
                moment(raw_end, where=f"{where}[{self.end_key!r}]") if raw_end is not None else None
            ),
            value=price(raw_value, where=f"{where}[{self.value_key!r}]") * Decimal(self.scale),
        )


__all__ = ["GenericList"]
