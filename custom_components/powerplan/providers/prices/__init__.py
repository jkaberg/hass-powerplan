"""`PriceSource` implementations (D1 §3).

All three of D1 §3's names, plus the wrapper the format table needed:
`nordpool_action` (the core Nord Pool integration's read-only response action),
`entity` (any price entity, through one `formats/` adapter), `action` (any price
*action*, through one `formats/` adapter - `design/DECISIONS.md` D-0101) and
`manual` (a flat or daily price the household typed, for the carriers no
integration publishes).

Market publication clocks are data in `markets.py`, never literals (D-0100).
"""

from . import formats
from .action import ActionSource, async_response_action
from .base import (
    FetchOutcome,
    FetchReport,
    Interval,
    PriceSource,
    RawStore,
    SourceAuthError,
    SourceDataError,
    SourceEmptyError,
    SourceError,
    SourceParseError,
    SourceUnavailableError,
    fetch_missing,
    local_day_bounds,
    normalise,
    within_local_day,
)
from .entity import EntitySource
from .manual import ManualSource
from .nordpool_action import NordpoolActionSource, async_get_prices_for_date

__all__ = [
    "ActionSource",
    "EntitySource",
    "FetchOutcome",
    "FetchReport",
    "Interval",
    "ManualSource",
    "NordpoolActionSource",
    "PriceSource",
    "RawStore",
    "SourceAuthError",
    "SourceDataError",
    "SourceEmptyError",
    "SourceError",
    "SourceParseError",
    "SourceUnavailableError",
    "async_get_prices_for_date",
    "async_response_action",
    "fetch_missing",
    "formats",
    "local_day_bounds",
    "normalise",
    "within_local_day",
]
