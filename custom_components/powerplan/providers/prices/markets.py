"""Market clocks as data: when each day-ahead auction publishes (D1 §5.1, D-0100).

This is the only module in the integration that writes an IANA timezone name
down, and the rule it exists to keep is simple: **the site's timezone is never a
literal** - it comes from `hass.config.time_zone`, through the site flow - and a
*market's* timezone is not the site's. Nord Pool's result lands at about 13:00
CET whether the house is in Trondheim or in Rome, so the publication clock is a
fact about the market and belongs in a table, next to the areas it applies to.

A user whose market changes its clock, or whose source publishes early, overrides
both halves under Advanced (D1 §6): every source that has a `Publication` renders
`publication_tz` and `publication_time`, defaulting to the derived values.

`tests/providers/prices/test_no_timezone_literals.py` asserts that no other
module under `custom_components/powerplan/` contains a zone name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import TYPE_CHECKING, Final

from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.pricing import Publication

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Day-ahead results are published shortly after noon in the market's own zone;
#: 13:00 is the time D1 §5.1 schedules against, late enough for a delayed auction
#: and early enough to leave the evening for retries.
DAY_AHEAD_LOCAL_TIME: Final = time(13, 0)

#: The zone the Central European day-ahead auctions settle in. Nord Pool's whole
#: area list, EPEX's Dutch and Belgian areas and Tibber's markets are all on it;
#: it is written once, here, and derived everywhere else.
CET: Final = "Europe/Oslo"

#: The Dutch day-ahead clock, for the two integrations that only serve it.
NL: Final = "Europe/Amsterdam"


@dataclass(frozen=True, slots=True)
class MarketClock:
    """When one market publishes tomorrow, in that market's own zone."""

    tz: str
    local_time: time = DAY_AHEAD_LOCAL_TIME

    def publication(self, *, tz: str = "", local_time: time | str | None = None) -> Publication:
        """Return the `Publication`, with the user's Advanced override applied.

        An empty override is what the flow saves when the user leaves the field
        alone, which is what "default: derived" means in D1 §6.
        """
        chosen = dt_util.parse_time(local_time) if isinstance(local_time, str) else local_time
        return Publication(local_time=chosen or self.local_time, tz=tz or self.tz)


CET_DAY_AHEAD: Final = MarketClock(CET)
NL_DAY_AHEAD: Final = MarketClock(NL)

#: Nord Pool's bidding areas, in the order D1 §6's prices step offers them. The
#: authoritative list is `pynordpool.AREAS`, which the core integration's action
#: validates against, so a code this tuple gets wrong fails the fetch rather than
#: the flow (D-0086).
NORDPOOL_AREAS: Final = (
    "NO1", "NO2", "NO3", "NO4", "NO5",
    "SE1", "SE2", "SE3", "SE4",
    "FI",
    "DK1", "DK2",
    "EE", "LV", "LT",
    "NL", "BE", "DE-LU", "FR", "AT",
)  # fmt: skip

#: Each area with the clock its day-ahead result lands on. One auction, one
#: clock: every Nord Pool area clears in the same CET session.
NORDPOOL_MARKETS: Final[Mapping[str, MarketClock]] = dict.fromkeys(NORDPOOL_AREAS, CET_DAY_AHEAD)

#: Tibber sells into the Nord Pool and EPEX day-ahead markets, all on CET.
TIBBER_MARKET: Final = CET_DAY_AHEAD

#: EnergyZero and easyEnergy serve the Dutch market and nothing else.
DUTCH_MARKET: Final = NL_DAY_AHEAD


__all__ = [
    "CET_DAY_AHEAD",
    "DAY_AHEAD_LOCAL_TIME",
    "DUTCH_MARKET",
    "NL_DAY_AHEAD",
    "NORDPOOL_MARKETS",
    "TIBBER_MARKET",
    "MarketClock",
]
