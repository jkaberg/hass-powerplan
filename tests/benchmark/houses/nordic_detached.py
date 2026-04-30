"""`nordic_detached@1` - the v1 reference house (D9 §5.9), one baseline.

The spec is `tests/builders/houses.py`'s `nordic_detached()` (every load the
house will ever have, each number with its source in `HOUSE_SOURCES` and the
simulators' `SOURCES`); this module is where the benchmark names it and says
which loads a build controls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.builders.houses import ALL_LOADS, HOUSE_ID, HOUSE_SOURCES, nordic_detached

if TYPE_CHECKING:
    from datetime import date

    from tests.benchmark.year import SyntheticYear
    from tests.builders.houses import House

NAME = "nordic_detached"
VERSION = HOUSE_ID
SOURCES = HOUSE_SOURCES

#: What this build steers: every load, because the core has every type (D4
#: complete). A build that cannot control a type lists it out and its
#: `controlled_share` falls (D9 §9 11).
CONTROLLED: frozenset[str] = frozenset(ALL_LOADS)


def build(year: SyntheticYear, start: date, *, seed: int) -> House:
    """Return the house at `start` of `year`, its generators seeded for the year."""
    return nordic_detached(
        seed=seed,
        start=year.start,
        price_regimes=year.price_regimes,
        weather_events=year.weather_events,
        controlled=CONTROLLED,
    )
