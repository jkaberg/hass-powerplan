"""`PriceSource` implementations (D1 §3).

v1 ships two of the three D1 §3 names: `nordpool_action` (the core Nord Pool
integration's read-only response action) and `entity` (any price entity, through
one `formats/` adapter). `manual.py` - a flat or daily price typed by the user for
gas, oil or district heat - lands with the other carriers, and the remaining ten
format rows with WP4.4.
"""

from . import formats
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
    normalise,
)
from .entity import EntitySource
from .nordpool_action import NordpoolActionSource, async_get_prices_for_date

__all__ = [
    "EntitySource",
    "FetchOutcome",
    "FetchReport",
    "Interval",
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
    "fetch_missing",
    "formats",
    "normalise",
]
