"""The core Nord Pool integration's `get_prices_for_date` action (D1 §2, §3).

The core `nordpool` integration publishes no forecast attributes - its sensors
carry `current_price`, `next_price` and the day's min/max and nothing else (see
`tests/fixtures/captured/nordpool_core_no3.json`). Tomorrow's curve is only
reachable through its response action, which is registered
`SupportsResponse.ONLY`: it reads prices through the integration's own client and
writes nothing at all.

**INV-3.** That action call is the one `hass.services.async_call` outside
`writegate.py` and `notifications.py`, and it lives in
`async_get_prices_for_date` below, alone, with `return_response=True`. The
single-writer rule is unchanged: nothing here actuates a device, and
`tests/core/invariants/test_single_writer.py` asserts both halves - that this is
the only file outside the gate that calls an action, and that every call in it is
read-only. See `design/DECISIONS.md` D-0080.

Response shape, from `homeassistant/components/nordpool/services.py` at 2026.4.0:
`{area: [{"start": iso, "end": iso, "price": float}]}`, with `price` in the
requested currency **per MWh** - the integration's own sensors divide by 1000 for
their `<CUR>/kWh` state - and `{area: []}` when the upstream response was empty.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar, Final

from homeassistant.exceptions import HomeAssistantError, ServiceNotFound, ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import (
    Carrier,
    Direction,
    Field,
    FieldKind,
    Publication,
    RawSlot,
)
from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

from .base import (
    Interval,
    SourceAuthError,
    SourceEmptyError,
    SourceParseError,
    SourceUnavailableError,
    normalise,
)
from .markets import CET_DAY_AHEAD, NORDPOOL_MARKETS

if TYPE_CHECKING:
    from datetime import date, datetime, time, tzinfo

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing import Schema

_LOGGER = logging.getLogger(__name__)

#: The integration and action this source reads through.
NORDPOOL_DOMAIN: Final = "nordpool"
SERVICE_GET_PRICES_FOR_DATE: Final = "get_prices_for_date"

#: The areas D1 §6's prices step offers, verbatim - and, per area, the clock its
#: day-ahead result lands on. Nord Pool publishes about 13:00 in the *market's*
#: zone (CET/CEST) whatever zone the house is in, so that zone is data in
#: `markets.py` and never a literal here (`design/DECISIONS.md` D-0100).
AREAS: Final = tuple(NORDPOOL_MARKETS)

#: The auth failure the action's own `translation_key` reports (services.py).
_AUTH_KEY: Final = "authentication_error"


async def async_get_prices_for_date(
    hass: HomeAssistant,
    *,
    config_entry_id: str,
    day: date,
    areas: tuple[str, ...],
    currency: str,
) -> dict[str, Any]:
    """Invoke Nord Pool's read-only response action (INV-3, D-0080).

    The only `hass.services.async_call` outside the write gate. It is
    `SupportsResponse.ONLY`, so `return_response=True` is not an option but the
    contract, and `blocking=True` because a response is the whole point.
    """
    data = {
        "config_entry": config_entry_id,
        "date": day.isoformat(),
        "areas": list(areas),
        "currency": currency,
    }
    try:
        response = await hass.services.async_call(
            NORDPOOL_DOMAIN,
            SERVICE_GET_PRICES_FOR_DATE,
            data,
            blocking=True,
            return_response=True,
        )
    except ServiceNotFound as err:
        raise SourceUnavailableError(
            "the Nord Pool integration is not set up, so "
            f"{NORDPOOL_DOMAIN}.{SERVICE_GET_PRICES_FOR_DATE} does not exist"
        ) from err
    except ServiceValidationError as err:
        if err.translation_key == _AUTH_KEY:
            raise SourceAuthError(f"Nord Pool refused the credentials: {err}") from err
        raise SourceUnavailableError(f"Nord Pool could not be reached: {err}") from err
    except HomeAssistantError as err:
        raise SourceUnavailableError(f"{SERVICE_GET_PRICES_FOR_DATE} failed: {err}") from err

    if not isinstance(response, dict):
        raise SourceParseError(
            f"{SERVICE_GET_PRICES_FOR_DATE} answered with "
            f"{type(response).__name__}, not a mapping of areas"
        )
    return response


class NordpoolActionSource:
    """v1's Nord Pool source: the core integration's response action (D1 §3)."""

    key: ClassVar[str] = "nordpool_action"
    schema: ClassVar[Schema] = (
        Field(key="config_entry", kind=FieldKind.TEXT, required=True),
        Field(key="area", kind=FieldKind.SELECT, options=AREAS, required=True),
        Field(key="currency", kind=FieldKind.TEXT, required=True),
        Field(key="publication_tz", kind=FieldKind.TEXT, default=None, advanced=True),
        Field(key="publication_time", kind=FieldKind.TIME, default=None, advanced=True),
    )

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry_id: str,
        area: str,
        currency: str,
        site_currency: str,
        tz: tzinfo,
        fx_rate: Decimal | None = None,
        publication_tz: str = "",
        publication_time: time | str | None = None,
    ) -> None:
        """Bind the source to one Nord Pool config entry and one area."""
        self._hass = hass
        self._config_entry_id = config_entry_id
        self._area = area
        self._currency = currency
        self._site_currency = site_currency
        self._tz = tz
        self._fx_rate = fx_rate
        self._publication_tz = publication_tz
        self._publication_time = publication_time
        self.carrier = Carrier.ELECTRICITY
        self.direction = Direction.IMPORT

    def publication(self) -> Publication:
        """Return the area's publication window: about 13:00 market time (INV-6).

        Derived from the area, never from the site's own zone, and overridable
        under Advanced for a market that moves its auction (D1 §6, D-0100).
        """
        clock = NORDPOOL_MARKETS.get(self._area, CET_DAY_AHEAD)
        return clock.publication(tz=self._publication_tz, local_time=self._publication_time)

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return the action's own unit: the requested currency per MWh, major."""
        return (self._currency, EnergyUnit.MWH, Magnitude.MAJOR)

    async def fetch(self, day: date) -> list[RawSlot]:
        """Ask the action for one local day and normalise its entries (D1 §5.2)."""
        response = await async_get_prices_for_date(
            self._hass,
            config_entry_id=self._config_entry_id,
            day=day,
            areas=(self._area,),
            currency=self._currency,
        )

        entries = response.get(self._area)
        if entries is None:
            raise SourceParseError(
                f"{SERVICE_GET_PRICES_FOR_DATE} answered for {sorted(response)}, "
                f"not for {self._area}"
            )
        if not entries:
            # `{area: []}` is what the action returns on an empty upstream
            # response - tomorrow before publication, or a delayed auction.
            raise SourceEmptyError(f"Nord Pool has published nothing for {self._area} on {day}")

        currency, energy, magnitude = self.native_unit()
        slots = normalise(
            (_interval(entry) for entry in entries),
            source=self.key,
            currency=currency,
            site_currency=self._site_currency,
            energy=energy,
            magnitude=magnitude,
            source_tz=self._tz,
            fetched_at=dt_util.utcnow(),
            fx_rate=self._fx_rate,
        )
        _LOGGER.debug("Nord Pool %s: %d slots for %s", self._area, len(slots), day)
        return list(slots)


def _interval(entry: Any) -> Interval:
    """Turn one `{start, end, price}` entry into an `Interval`."""
    if not isinstance(entry, dict):
        raise SourceParseError(f"a Nord Pool entry is a {type(entry).__name__}, not a slot")
    try:
        return Interval(
            start=_moment(entry["start"]),
            end=_moment(entry["end"]),
            value=_decimal(entry["price"]),
        )
    except KeyError as err:
        raise SourceParseError(f"a Nord Pool entry has no {err.args[0]!r}") from err


def _moment(raw: Any) -> datetime:
    """Parse one ISO-8601 boundary the action serialised."""
    parsed = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise SourceParseError(f"{raw!r} is not a Nord Pool slot boundary")
    return parsed


def _decimal(raw: Any) -> Decimal:
    """Convert one price to `Decimal` through `str`, never through binary float."""
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as err:
        raise SourceParseError(f"{raw!r} is not a Nord Pool price") from err


__all__ = ["NordpoolActionSource", "async_get_prices_for_date"]
