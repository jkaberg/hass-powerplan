"""`EnergyStore` - an EV battery or a home battery (D4 §4.3, §5.7).

Level is SoC in percent, capacity is kWh, and the efficiency is charged on the
way in: `(target − soc) × capacity × usable / charge_eff`.

An **unknown SoC is `None`, never a zero**. The EV without a SoC sensor is
exactly this case and it is not pretended: the plan then works from the
`kwh_to_add` knob or from time alone (D4 §5.11). A store that answered 0 kWh
would tell the planner the car was full.

`max_discharge_w` and `reserve_soc` carry V2H and the home battery; the `battery`
type is phase 5 and V2H is v1.x, but the fields are §4.3's and the EV's signed
`min_w` reads them.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .base import StoreCtx, StoreDirection

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["BAND_KEYS", "EnergyStore", "band_key"]

#: A car's charging current bands, lowest first (D10 §5.6, D-0776): a 1-phase 6 A
#: session loses 13-24 % against 5-13 % at 3-phase 10-16 A (Reick et al. 2021).
BAND_KEYS: Final = ("6_9", "10_15", "16")
#: A band starts at its first ampere: 10 A, 16 A. Below 10 is the lowest band.
_BAND_FROM_A: Final = (10.0, 16.0)


def band_key(phases: int, amps: float) -> str:
    """Return the efficiency band of `amps` on `phases` phases: `1p_6_9`, `3p_16`, ... (D-0776)."""
    index = sum(amps >= edge for edge in _BAND_FROM_A)
    return f"{phases}p_{BAND_KEYS[index]}"


@dataclass(frozen=True, slots=True)
class EnergyStore:
    """A battery, in percent of a known capacity (D4 §4.3)."""

    capacity_kwh: float
    min_soc: float
    max_soc: float
    max_charge_w: float
    usable_fraction: float = 1.0
    charge_eff: float = 0.90
    discharge_eff: float = 0.95
    reserve_soc: float | None = None
    max_discharge_w: float = 0.0
    direction: StoreDirection = "heat"
    #: An EV's measured efficiency per phase count and current band, `(band_key, η)` (D10 §5.6).
    eta_by_band: tuple[tuple[str, float], ...] = ()
    phases: int = 1
    w_per_amp: float = 0.0

    def eta_at(self, w: float) -> float:
        """Return the wall-to-battery efficiency of charging at `w` watts (D-0776).

        The band's own fit where there is one, else the session-wide `charge_eff`.
        """
        if not self.eta_by_band or self.w_per_amp <= 0.0:
            return self.charge_eff
        key = band_key(self.phases, w / self.w_per_amp)
        return dict(self.eta_by_band).get(key, self.charge_eff)

    def capacity_kwh_per_unit(self) -> float:
        """KWh per percentage point of SoC."""
        return self.capacity_kwh * self.usable_fraction / 100.0

    def required_kwh(
        self, level_now: float | None, target: float, deadline: datetime | None, ctx: StoreCtx
    ) -> float | None:
        """KWh from the wall to move SoC to `target`, or `None` when SoC is unknown."""
        if level_now is None:
            return None
        bounded = min(target, self.max_soc)
        points = max(0.0, bounded - level_now)
        return points * self.capacity_kwh_per_unit() / self.charge_eff

    def max_level(self) -> float:
        """Return the configured charge ceiling - 80 % is a battery-health norm (INV-56)."""
        return self.max_soc

    def min_level(self) -> float:
        """Return the floor the household keeps: "always enough to get to work"."""
        return self.min_soc

    def coast_hours(self, level_now: float, floor: float, ctx: StoreCtx) -> float | None:
        """Return `None`: a parked battery does not self-discharge on a planning horizon."""
        return None

    def deliverable_kwh(self, level_now: float | None) -> float | None:
        """KWh available above the reserve, out of the battery (V2H, v1.x)."""
        if level_now is None:
            return None
        floor = self.reserve_soc if self.reserve_soc is not None else self.min_soc
        return max(0.0, level_now - floor) * self.capacity_kwh_per_unit() * self.discharge_eff
