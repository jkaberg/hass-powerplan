"""`au_solar@1` - D9 §5.9's solar house: panels and a battery on a wholesale price.

The spec is `tests/builders/houses.py`'s `au_solar()`: the twelve loads of
`nordic_detached` in Sydney, 6.6 kWp, a 13.5 kWh battery, the NEM's duck curve
passed through (`SOLAR_GLUT` in AUD) and exported at it. Like `nl_pv`, the house
brings its own price regime; only the year's start and weather events are taken
from the shared calendar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.builders.houses import ALL_LOADS, AU_SOLAR_HOUSE_ID, AU_SOLAR_SOURCES, au_solar

if TYPE_CHECKING:
    from datetime import date

    from tests.benchmark.year import SyntheticYear
    from tests.builders.houses import House

NAME = "au_solar"
VERSION = AU_SOLAR_HOUSE_ID
SOURCES = AU_SOLAR_SOURCES

#: Every load this house has, steered the same way `nordic_detached` is.
CONTROLLED: frozenset[str] = frozenset(ALL_LOADS)


def build(year: SyntheticYear, start: date, *, seed: int) -> House:
    """Return the house at `start` of `year`, its generators seeded for the year."""
    return au_solar(
        seed=seed,
        start=year.start,
        weather_events=year.weather_events,
        controlled=CONTROLLED,
    )
