"""`nl_pv@2` - D9 §5.9's second "other house": PV, `ContractedPower`, EPEX.

The spec is `tests/builders/houses.py`'s `nl_pv()`; this module names it for
the benchmark and says which loads a build controls, exactly like
`nordic_detached.py`. Unlike that house, `nl_pv()` supplies its **own** price
regime (`SOLAR_GLUT`, D-0312) rather than the year's - `y2026_27`'s own
regimes are Norwegian (NOK, no midday trough) and would misrepresent the one
thing this house exists to prove, so only `year.start`/`.weather_events` are
taken from the shared calendar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.builders.houses import ALL_LOADS, NL_PV_HOUSE_ID, NL_PV_SOURCES, nl_pv

if TYPE_CHECKING:
    from datetime import date

    from tests.benchmark.year import SyntheticYear
    from tests.builders.houses import House

NAME = "nl_pv"
VERSION = NL_PV_HOUSE_ID
SOURCES = NL_PV_SOURCES

#: Every load this house has, steered the same way `nordic_detached` is.
CONTROLLED: frozenset[str] = frozenset(ALL_LOADS)


def build(year: SyntheticYear, start: date, *, seed: int) -> House:
    """Return the house at `start` of `year`, its generators seeded for the year."""
    return nl_pv(
        seed=seed,
        start=year.start,
        weather_events=year.weather_events,
        controlled=CONTROLLED,
    )
