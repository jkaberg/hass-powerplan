"""D1 - pricing: curve composition, modifiers, forecasters, events (HLD §6.1).

The public API D1 §3 names: `build_curve`, the curve statistics (methods on
`PriceCurve` itself - `core/model.py`, `design/DECISIONS.md` D-0030), the fetch
schedule, the hysteresis policy, the event store and the two registries.
Importing this package registers every modifier and forecaster that ships, so
`modifiers.keys()` and `forecasters.keys()` answer what a site can be
configured with.

Providers (`providers/prices/`, `providers/events/`) are WP1.2 and live outside
`core/`: this package never reaches out for anything. Every value it needs
arrives in the `RawSlot`s and the `PriceContext`.
"""

from . import forecasters, modifiers
from .compose import DEFAULT_MAX_AGE, CoverageError, build_curve
from .context import HolidayCalendar, PriceContext
from .events import Event, EventKind, EventStore
from .hysteresis import HysteresisPolicy
from .model import (
    Carrier,
    Confidence,
    Direction,
    Field,
    FieldKind,
    Money,
    PriceCurve,
    RawSlot,
    Schema,
    Slot,
)
from .schedule import (
    Publication,
    backoff,
    never_on_the_hour,
    next_fetch_at,
    next_hole_check_at,
    next_retry_at,
)

__all__ = [
    "DEFAULT_MAX_AGE",
    "Carrier",
    "Confidence",
    "CoverageError",
    "Direction",
    "Event",
    "EventKind",
    "EventStore",
    "Field",
    "FieldKind",
    "HolidayCalendar",
    "HysteresisPolicy",
    "Money",
    "PriceContext",
    "PriceCurve",
    "Publication",
    "RawSlot",
    "Schema",
    "Slot",
    "backoff",
    "build_curve",
    "forecasters",
    "modifiers",
    "never_on_the_hour",
    "next_fetch_at",
    "next_hole_check_at",
    "next_retry_at",
]
