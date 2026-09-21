"""`fi_linear@1` - one of D9 §5.9's "other houses".

The spec is `tests/builders/houses.py`'s `fi_linear()`; this module names it for the
benchmark and says which loads a build controls, exactly like `be_quarter.py`.
Only `year.start`/`.weather_events` come from the shared calendar: the year's
price regimes are Norwegian and would overwrite this house's own (D-0312).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.builders.houses import ALL_LOADS, FI_LINEAR_HOUSE_ID, FI_LINEAR_SOURCES, fi_linear

if TYPE_CHECKING:
    from datetime import date

    from tests.benchmark.year import SyntheticYear
    from tests.builders.houses import House

NAME = "fi_linear"
VERSION = FI_LINEAR_HOUSE_ID
SOURCES = FI_LINEAR_SOURCES

#: Every load this house has, steered the same way `nordic_detached` is.
CONTROLLED: frozenset[str] = frozenset(ALL_LOADS)


def build(year: SyntheticYear, start: date, *, seed: int) -> House:
    """Return the house at `start` of `year`, its generators seeded for the year."""
    del start
    return fi_linear(
        seed=seed,
        start=year.start,
        weather_events=year.weather_events,
        controlled=CONTROLLED,
    )
