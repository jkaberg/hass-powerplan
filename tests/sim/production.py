"""A rooftop PV array: rated kWp times today's irradiance, PVWatts-simple (D9 §5.9 `nl_pv`).

A pure function of `t` given the seed, through `weather.at(t).solar_w_per_m2` -
the same clear-sky, sun-angle, seeded-cloud irradiance every simulated house's
`Env` already carries (`sim/weather.py`). No separate solar model: two houses
under the same weather instance see irradiance consistent with each other, the
way two rooms under the same `WeatherSim` already do.

Output is **negative** watts (export), added straight into `HouseDriver.step`'s
running `total_w` beside `uncontrolled` - the meter integrates a house with
solar the same way it integrates one without, because `command` was always
signed (`sim/base.py`, `sim/meter.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .weather import WeatherSim

if TYPE_CHECKING:
    from datetime import datetime

#: Standard test condition irradiance, W/m² - a PVWatts DC rating is defined
#: at this level (`sim/weather.py`'s own `CLEAR_SKY_W_PER_M2` is the same figure).
STC_W_PER_M2 = 1000.0
#: NREL PVWatts' default "System Losses" derate, 1 − 0.1408 (soiling 2 %,
#: shading 3 %, mismatch 2 %, wiring 2 %, connections 0.5 %, light-induced
#: degradation 1.5 %, nameplate 1 %, availability 3 %; age and snow 0 % for a
#: new array) - https://pvwatts.nrel.gov/pvwatts.php, "System Losses" default.
PERFORMANCE_RATIO = 1.0 - 0.1408

SOURCES: dict[str, str] = {
    "STC_W_PER_M2": "the PV industry's standard test condition, 1000 W/m² (IEC 61215)",
    "PERFORMANCE_RATIO": (
        "NREL PVWatts Calculator, default 'System Losses' 14.08 % "
        "(https://pvwatts.nrel.gov/pvwatts.php) — a fixed derate, no explicit "
        "temperature-coefficient model; replaced by a module datasheet's own "
        "NOCT/temperature curve if refined"
    ),
}


@dataclass(slots=True)
class ProductionSim:
    """A `rated_kwp` array under `weather`'s own irradiance, negative (export) watts."""

    rated_kwp: float
    weather: WeatherSim
    performance_ratio: float = PERFORMANCE_RATIO

    def at(self, t: datetime) -> float:
        """Export watts at `t` (≤ 0), clipped at the array's own rating."""
        irradiance = self.weather.at(t).solar_w_per_m2
        dc_w = self.rated_kwp * 1000.0 * (irradiance / STC_W_PER_M2) * self.performance_ratio
        return -min(dc_w, self.rated_kwp * 1000.0)
