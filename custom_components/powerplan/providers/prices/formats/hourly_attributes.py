"""`<prefix>{HH}h` attributes, today and tomorrow (D1 §2, second-to-last row).

The generic form of the row `pvpc` is a special case of: a sensor that publishes
one attribute per hour - `price_00h`, `rate_07h`, `tariff_23h` - rather than a
list of slots. Template sensors and several national integrations do it this way,
and the pattern is regular enough to configure rather than to write an adapter
for each.

Two things the attribute names do not say, and this module has to supply.

**Which day.** An hour key names an hour and no date, so the date is today in
*Home Assistant's own* timezone (`dt_util.now()`), which is the site's zone and
the zone the publishing integration wrote those hours in. It is read from the
clock rather than configured because "today" is not a setting
(`design/DECISIONS.md` D-0104).

**Which of two repeated hours.** On the 25-hour day a market publishes an extra
key, `price_02h_d` beside `price_02h`. The `_d` one is the second occurrence, so
it is built with `fold=1`; both are naive local times and `normalise` resolves
them against the source's zone (D1 §5.2, §5.8). On the 23-hour day the key for
the hour that does not exist is simply absent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, ClassVar, Final

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Field, FieldKind, Schema
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude
from custom_components.powerplan.providers.prices.base import Interval, SourceParseError

from .base import FormatKind, ParsedPrices, price
from .registry import register

if TYPE_CHECKING:
    from homeassistant.core import State

#: What is left of an hour key once its prefix is taken off: `07h`, or `02h_d`
#: for the repeat of the fall-back hour.
HOUR_KEY: Final = re.compile(r"^(?P<hour>\d{2})h(?P<repeat>_d)?$")

#: `23h` is the last hour a key can name; `24h` is not an hour of a day.
LAST_HOUR: Final = 23


def hour_intervals(state: State, *, prefix: str, day: date) -> list[Interval]:
    """Read every `<prefix>{HH}h` attribute as an hour of the local day `day`.

    The value is left without an end: the slot ends where the next one begins, so
    a missing hour stays a hole and a DST day keeps its real shape (INV-7).
    """
    out: list[Interval] = []
    for key, value in state.attributes.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        match = HOUR_KEY.match(key[len(prefix) :])
        if match is None:
            continue
        if value is None:
            # An hour the source could not price: a hole, never a zero price.
            continue
        hour = int(match["hour"])
        if hour > LAST_HOUR:
            raise SourceParseError(f"{state.entity_id}.{key} is not an hour of a day")
        out.append(
            Interval(
                start=datetime.combine(day, time(hour=hour)).replace(
                    fold=1 if match["repeat"] else 0
                ),
                end=None,
                value=price(value, where=f"{state.entity_id}.{key}"),
            )
        )
    return out


@register
@dataclass(frozen=True, slots=True)
class HourlyAttributes:
    """Reads a user-described `<prefix>{HH}h` sensor (D1 §2)."""

    key: ClassVar[str] = "hourly_attributes"
    platform: ClassVar[str | None] = None
    kind: ClassVar[FormatKind] = FormatKind.ATTRIBUTES
    schema: ClassVar[Schema] = (
        Field(key="currency", kind=FieldKind.TEXT, required=True),
        Field(key="today_prefix", kind=FieldKind.TEXT, default="price_", required=True),
        Field(key="tomorrow_prefix", kind=FieldKind.TEXT, default=""),
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
    today_prefix: str = "price_"
    tomorrow_prefix: str = ""
    energy_unit: EnergyUnit = field(default=EnergyUnit.KWH)
    magnitude: Magnitude = field(default=Magnitude.MAJOR)

    def parse(self, state: State) -> ParsedPrices:
        """Return the hours the sensor publishes under the configured prefixes."""
        today = dt_util.now().date()
        intervals = hour_intervals(state, prefix=self.today_prefix, day=today)
        if self.tomorrow_prefix:
            intervals += hour_intervals(
                state, prefix=self.tomorrow_prefix, day=today + timedelta(days=1)
            )
        if not intervals:
            raise SourceParseError(
                f"{state.entity_id} has no {self.today_prefix}XXh attribute; "
                f"it has {sorted(state.attributes)}"
            )

        return ParsedPrices(
            intervals=tuple(intervals),
            currency=self.currency,
            energy=EnergyUnit(self.energy_unit),
            magnitude=Magnitude(self.magnitude),
        )


__all__ = ["HourlyAttributes", "hour_intervals"]
