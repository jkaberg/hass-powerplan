"""`ActionSource`: prices that live behind a response action (D1 §3, D-0101).

Three integrations keep their prices where no entity can be read - Tibber,
EnergyZero and easyEnergy publish a *response action* and nothing else - and the
Nord Pool core integration does the same (`nordpool_action.py`, which predates
this module and keeps its own client because it also owns the area table).

**INV-3.** `async_response_action` below is the one `hass.services.async_call` in
this module and the only one the `formats/` rows may reach; every one of these
actions is registered `SupportsResponse.ONLY`, so Home Assistant itself refuses
to run it as a write, and this file is on the allowlist in
`tests/core/invariants/test_single_writer.py` under the same amended INV-3 the
Nord Pool source uses (`design/DECISIONS.md` D-0080). Putting the call here rather
than in each adapter is what keeps that allowlist at two entries instead of five.

Everything else is `EntitySource`'s shape: normalise once (D1 §5.2), return the
local day that was asked for, and let a day with nothing in it be
`SourceEmptyError` so §5.1's backoff can try again.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError, ServiceNotFound, ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Carrier, Direction

from .base import (
    SourceAuthError,
    SourceEmptyError,
    SourceParseError,
    SourceUnavailableError,
    normalise,
    within_local_day,
)

if TYPE_CHECKING:
    from datetime import date, tzinfo
    from decimal import Decimal

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.pricing import Publication, RawSlot
    from custom_components.powerplan.core.pricing.normalise import EnergyUnit, Magnitude

    from .formats import ActionFormat

_LOGGER = logging.getLogger(__name__)

#: The `translation_key` an integration uses when it is the credentials that
#: failed rather than the request - the one failure a retry cannot fix.
AUTH_KEYS = frozenset({"authentication_error", "invalid_auth", "auth_failed"})


async def async_response_action(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Invoke one integration's read-only response action (INV-3, D-0080).

    `blocking=True` because a response is the whole point, and
    `return_response=True` on every call site in this file, which is the
    machine-checked half of the INV-3 exemption: an action called without it
    could be a write, and a write belongs behind the gate (INV-20, INV-24).
    """
    try:
        response = await hass.services.async_call(
            domain,
            service,
            data,
            blocking=True,
            return_response=True,
        )
    except ServiceNotFound as err:
        raise SourceUnavailableError(
            f"the {domain} integration is not set up, so {domain}.{service} does not exist"
        ) from err
    except ServiceValidationError as err:
        if err.translation_key in AUTH_KEYS:
            raise SourceAuthError(f"{domain} refused the credentials: {err}") from err
        raise SourceUnavailableError(f"{domain} could not be reached: {err}") from err
    except HomeAssistantError as err:
        raise SourceUnavailableError(f"{domain}.{service} failed: {err}") from err

    if not isinstance(response, dict):
        raise SourceParseError(
            f"{domain}.{service} answered with {type(response).__name__}, not a mapping"
        )
    return response


class ActionSource:
    """Reads prices through one `ActionFormat` adapter (D1 §3, §5.2)."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        adapter: ActionFormat,
        site_currency: str,
        tz: tzinfo,
        carrier: Carrier = Carrier.ELECTRICITY,
        direction: Direction = Direction.IMPORT,
        fx_rate: Decimal | None = None,
    ) -> None:
        """Bind the source to one adapter and the site's currency and zone."""
        self._hass = hass
        self._adapter = adapter
        self._site_currency = site_currency
        self._tz = tz
        self._fx_rate = fx_rate
        self.carrier = carrier
        self.direction = direction
        # The row's own key, not the wrapper's: the raw store keys slots by
        # source, and two action-backed sources in one site must not collide
        # (the same reason `carrier` is per instance - D-0086).
        self.key = adapter.key
        self.schema = adapter.schema

    def publication(self) -> Publication | None:
        """Return the market's publication window, from the adapter (INV-6)."""
        return self._adapter.publication()

    def native_unit(self) -> tuple[str, EnergyUnit, Magnitude]:
        """Return what the action answers in (D1 §4)."""
        return self._adapter.native_unit()

    def entity_ids(self) -> frozenset[str]:
        """Return nothing: an action is not an entity, so there is nothing to watch."""
        return frozenset()

    async def fetch(self, day: date) -> list[RawSlot]:
        """Call the action for the local day `day` and normalise it (D1 §5.2)."""
        parsed = await self._adapter.fetch(self._hass, day, tz=self._tz)
        slots = normalise(
            parsed.intervals,
            source=self.key,
            currency=parsed.currency,
            site_currency=self._site_currency,
            energy=parsed.energy,
            magnitude=parsed.magnitude,
            source_tz=self._tz,
            fetched_at=dt_util.utcnow(),
            fx_rate=self._fx_rate,
        )

        wanted = within_local_day(slots, day, self._tz)
        if not wanted:
            raise SourceEmptyError(f"{self.key} has published nothing for {day}")
        _LOGGER.debug("%s: %d of %d slots are within %s", self.key, len(wanted), len(slots), day)
        return wanted


__all__ = ["ActionSource", "async_response_action"]
