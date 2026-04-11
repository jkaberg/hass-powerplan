"""`manual`: a flat or daily price the household types in (D1 §3, §6).

The source for the carriers no integration publishes. Gas, oil, district heat and
pellets are a number on a contract or a delivery note, and "my fixed contract" is
one for electricity too - a site on Norgespris or a three-year fixed deal has a
price nobody needs to fetch. D6 compares the cost of heating a tank with
electricity against heating it with gas, and that comparison needs the gas side to
exist as a curve like any other.

One slot per **local** day, which is what D1 §2 means by "electricity slots are
15/60 min; gas slots are daily": the slot is exactly as long as the day, so a DST
day is 23 or 25 hours and not 24 (INV-7). A date in `daily` overrides the flat
price for that day alone - the shape of an oil delivery, or of a district-heat
tariff that changes on the first of the month.

Nothing is fetched, so there is no `Publication` and no entity to subscribe to,
and nothing is clamped: a district-heat credit is a negative price like any other
(INV-51). The price is still normalised, which means a currency that is not the
site's is refused here exactly as it is for a market source (D1 §5.2) - a typed
number is not more trustworthy for having been typed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import (
    Carrier,
    Direction,
    Field,
    FieldKind,
    RawSlot,
)
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import Interval, local_day_bounds, normalise

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, tzinfo
    from decimal import Decimal

    from custom_components.powerplan.core.pricing import Publication, Schema

_LOGGER = logging.getLogger(__name__)


class ManualSource:
    """A price the user typed: flat, or per day (D1 §3)."""

    key: ClassVar[str] = "manual"
    schema: ClassVar[Schema] = (
        Field(key="price", kind=FieldKind.MONEY, required=True),
        Field(key="currency", kind=FieldKind.TEXT, required=True),
        Field(
            key="carrier",
            kind=FieldKind.SELECT,
            default=Carrier.ELECTRICITY.value,
            options=tuple(carrier.value for carrier in Carrier),
            required=True,
        ),
        Field(
            key="magnitude",
            kind=FieldKind.SELECT,
            default=Magnitude.MAJOR.value,
            options=tuple(magnitude.value for magnitude in Magnitude),
            advanced=True,
        ),
        Field(key="daily", kind=FieldKind.LIST, default=(), advanced=True),
    )

    def __init__(
        self,
        *,
        price: Decimal,
        currency: str,
        site_currency: str,
        tz: tzinfo,
        carrier: Carrier = Carrier.ELECTRICITY,
        direction: Direction = Direction.IMPORT,
        magnitude: Magnitude = Magnitude.MAJOR,
        daily: Mapping[date, Decimal] | None = None,
        fx_rate: Decimal | None = None,
    ) -> None:
        """Bind the source to one typed price and the site's currency."""
        self._price = price
        self._currency = currency
        self._site_currency = site_currency
        self._tz = tz
        self._magnitude = magnitude
        self._daily = dict(daily or {})
        self._fx_rate = fx_rate
        self.carrier = carrier
        self.direction = direction

    def publication(self) -> Publication | None:
        """Return `None`: a typed price is never fetched and never late (D1 §5.1)."""
        return None

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return what the user typed in: their currency, per kWh (D1 §6)."""
        return (self._currency, EnergyUnit.KWH, self._magnitude)

    def entity_ids(self) -> frozenset[str]:
        """Return nothing: there is no entity behind a typed price (INV-3)."""
        return frozenset()

    async def fetch(self, day: date) -> list[RawSlot]:
        """Return the one slot the local day `day` is priced at (D1 §2)."""
        start, end = local_day_bounds(day, self._tz)
        value = self._daily.get(day, self._price)
        slots = normalise(
            (Interval(start=start, end=end, value=value),),
            source=self.key,
            currency=self._currency,
            site_currency=self._site_currency,
            energy=EnergyUnit.KWH,
            magnitude=self._magnitude,
            source_tz=self._tz,
            fetched_at=dt_util.utcnow(),
            fx_rate=self._fx_rate,
        )
        _LOGGER.debug(
            "manual %s price for %s: %s %s per kWh",
            self.carrier,
            day,
            slots[0].value,
            slots[0].currency,
        )
        return list(slots)


__all__ = ["ManualSource"]
