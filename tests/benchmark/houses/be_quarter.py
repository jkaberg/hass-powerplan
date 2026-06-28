"""`be_quarter@1` - D9 §5.9's third "other house": Fluvius's rolling-12.

The spec is `tests/builders/houses.py`'s `be_quarter()`; this module names it
for the benchmark and says which loads a build controls, exactly like
`nordic_detached.py`. `be_quarter()` reuses `SPOT_LIKE` (in EUR) as its own
default when no regime is given, since this house's own point is the
capacity tariff's window length and rolling average, not a fitted Belgian
energy-price shape (D-0312) - only `year.start`/`.weather_events` come from
the shared calendar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.builders.houses import ALL_LOADS, BE_QUARTER_HOUSE_ID, BE_QUARTER_SOURCES, be_quarter

if TYPE_CHECKING:
    from datetime import date

    from tests.benchmark.year import SyntheticYear
    from tests.builders.houses import House

NAME = "be_quarter"
VERSION = BE_QUARTER_HOUSE_ID
SOURCES = BE_QUARTER_SOURCES

#: Every load this house has, steered the same way `nordic_detached` is.
CONTROLLED: frozenset[str] = frozenset(ALL_LOADS)


def build(year: SyntheticYear, start: date, *, seed: int) -> House:
    """Return the house at `start` of `year`, its generators seeded for the year."""
    return be_quarter(
        seed=seed,
        start=year.start,
        weather_events=year.weather_events,
        controlled=CONTROLLED,
    )
