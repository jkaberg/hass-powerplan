"""Price refresh with retry and a repair issue (D12 §5.15 F12).

After a restart the price sensors can stay unknown for a while, and every slot is
"stale" or "synthesised" until they rebuild: on the reference house that was 37 minutes
of planning on estimates while Nord Pool itself answered, and nothing said why.

`PriceRefresher` makes that self-healing and visible:
  * after the runtime's startup fetch, and whenever the Nord Pool entry (re)loads, it checks prices;
  * while no slot covering "now" is known it retries with back-off (1, 2, 5, 10, 15 min, then every 15);
  * after 30 min it raises the repair issue `prices_stale` (D8 §5.9's catalogue); it clears the issue
    as soon as prices are known again;
  * `button.<site>_refresh_prices` lets the price card's "Hent på nytt" trigger it (D12 §5.16 R3).

The refresher lives on the runtime (`runtime.price_refresher`, never `hass.data`), the fetch is the
runtime's own (`Runtime.refresh_prices`), and the issue goes through `repairs.async_report` like every
other (D-0580).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .repairs import async_clear, async_report

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

BACKOFF_S = (60, 120, 300, 600, 900)
ISSUE_AFTER = timedelta(minutes=30)
ISSUE_ID = "prices_stale"


class PriceRefresher:
    """Retry until the slot covering now is known; say so after `ISSUE_AFTER`."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        refresh: Callable[[], Awaitable[bool]],
        known_now: Callable[[], bool],
    ) -> None:
        """Bind to the site; `refresh` fetches, `known_now` says whether now's slot is known."""
        self.hass = hass
        self.entry = entry
        self._refresh = refresh
        self._known_now = known_now
        self._attempt = 0
        self._stale_since: datetime | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._retry: CALLBACK_TYPE | None = None
        self._busy = False

    @callback
    def async_setup(self) -> None:
        """Follow the Nord Pool entries' loads; the runtime's startup fetch reports through `observe`."""
        for np_entry in self.hass.config_entries.async_entries("nordpool"):
            on_change = getattr(np_entry, "async_on_state_change", None)  # HA ≥ 2025.3
            if on_change:
                self._unsubs.append(on_change(lambda e=np_entry: self._on_nordpool(e)))

    @callback
    def async_unload(self) -> None:
        """Stop listening and cancel a pending retry."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._retry:
            self._retry()
            self._retry = None

    @callback
    def observe(self, why: str) -> None:
        """Check the prices after a fetch the runtime made on its own (startup, a retry, a service)."""
        self._after(self._known_now(), why)

    @callback
    def _on_nordpool(self, np_entry: ConfigEntry) -> None:
        if np_entry.state is ConfigEntryState.LOADED:
            self.hass.async_create_background_task(
                self.async_refresh("nordpool_loaded"), "powerplan_price_refresh"
            )

    async def async_refresh(self, why: str) -> bool:
        """Fetch now; `False` while the slot covering now is still not known."""
        if self._busy:
            return False
        self._busy = True
        try:
            ok = await self._refresh()
        except Exception:
            _LOGGER.debug("Price refresh (%s) failed", why, exc_info=True)
            ok = False
        finally:
            self._busy = False
        ok = ok and self._known_now()
        self._after(ok, why)
        return ok

    @callback
    def _after(self, ok: bool, why: str) -> None:
        if ok:
            if self._stale_since:
                _LOGGER.info(
                    "Prices known again after %s (%s)", dt_util.utcnow() - self._stale_since, why
                )
            self._attempt = 0
            self._stale_since = None
            if self._retry:
                self._retry()
                self._retry = None
            async_clear(self.hass, self.entry.entry_id, ISSUE_ID)
            return
        now = dt_util.utcnow()
        self._stale_since = self._stale_since or now
        if now - self._stale_since >= ISSUE_AFTER:
            async_report(
                self.hass,
                self.entry.entry_id,
                ISSUE_ID,
                active=True,
                placeholders={"since": dt_util.as_local(self._stale_since).strftime("%H:%M")},
                entry_title=self.entry.title,
            )
        delay = BACKOFF_S[min(self._attempt, len(BACKOFF_S) - 1)]
        self._attempt += 1
        if self._retry:
            self._retry()
        self._retry = async_call_later(self.hass, delay, self._retry_now)

    @callback
    def _retry_now(self, _now: datetime) -> None:
        self._retry = None
        self.hass.async_create_background_task(
            self.async_refresh("retry"), "powerplan_price_refresh"
        )
