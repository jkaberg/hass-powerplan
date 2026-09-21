"""`fr_tempo@1` - one of D9 §5.9's "other houses".

The spec is `tests/builders/houses.py`'s `fr_tempo()`; this module names it for the
benchmark and says which loads a build controls, exactly like `be_quarter.py`.
Only `year.start`/`.weather_events` come from the shared calendar: the year's
price regimes are Norwegian and would overwrite this house's own (D-0312).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.builders.houses import ALL_LOADS, FR_TEMPO_HOUSE_ID, FR_TEMPO_SOURCES, fr_tempo

if TYPE_CHECKING:
    from datetime import date

    from tests.benchmark.year import SyntheticYear
    from tests.builders.houses import House

NAME = "fr_tempo"
VERSION = FR_TEMPO_HOUSE_ID
SOURCES = FR_TEMPO_SOURCES

#: Every load this house has, steered the same way `nordic_detached` is.
CONTROLLED: frozenset[str] = frozenset(ALL_LOADS)


def build(year: SyntheticYear, start: date, *, seed: int) -> House:
    """Return the house at `start` of `year`, its generators seeded for the year."""
    del start
    return fr_tempo(
        seed=seed,
        start=year.start,
        weather_events=year.weather_events,
        controlled=CONTROLLED,
    )
