"""Thermal stores: a slab, a room, a tank (D4 §4.3, §5.7).

Two numbers the design states outright, and both are asserted in the tests:

* 57.5 m² of slab under 50 mm of screed is **1.58 kWh/K** - area × depth × ρ ×
  cp / 3600, with ρ 2200 kg/m³ and cp 0.9 kJ/kgK for screed;
* 300 L from 45 to 75 °C at η 0.98 is **≈ 10.7 kWh** - litres × 4.186 × ΔK /
  3600 / η.

The loss term needs a fitted coefficient (D10 fits it from history). Without one
it is **skipped**, which understates what the store needs rather than inventing a
number: a conservative fill charges again next slot, an invented one charges at
the wrong hour and nobody finds out.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import StoreCtx, StoreDirection, hours_until

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["CP_SCREED", "RHO_SCREED", "WATER_KJ_PER_LK", "RoomStore", "SlabStore", "TankStore"]

#: Screed: density kg/m³ and specific heat kJ/kgK (D4 §4.3).
RHO_SCREED = 2200.0
CP_SCREED = 0.9

#: Water's specific heat, kJ per litre-kelvin.
WATER_KJ_PER_LK = 4.186

#: The effective building mass of light-weight (timber) construction, kWh/K per
#: m³ (D4 §5.7). The air alone would be 0.0003; heavy masonry is 2–3× this.
ROOM_MASS_KWH_PER_K_PER_M3 = 0.03


def _deficit(level_now: float, target: float, direction: StoreDirection) -> float:
    """How far there is to go, signed by direction and never negative (D4 §4.3)."""
    if direction == "cool":
        return max(0.0, level_now - target)
    return max(0.0, target - level_now)


def _loss_kwh(coeff_w_per_k: float | None, level_now: float, ctx: StoreCtx, hours: float) -> float:
    """Loss to the outside over `hours`, or 0 when nothing can compute it (§5.7)."""
    if coeff_w_per_k is None or ctx.outdoor_c is None or hours <= 0.0:
        return 0.0
    inside = ctx.indoor_c if ctx.indoor_c is not None else level_now
    return coeff_w_per_k * abs(inside - ctx.outdoor_c) * hours / 1000.0


@dataclass(frozen=True, slots=True)
class SlabStore:
    """A heated screed floor: the house's cheapest battery (D4 §4.3)."""

    area_m2: float
    screed_mm: float
    loss_coeff_w_per_k: float | None
    max_c: float
    min_c: float = 5.0
    rho: float = RHO_SCREED
    cp: float = CP_SCREED
    direction: StoreDirection = "heat"

    def capacity_kwh_per_unit(self) -> float:
        """KWh per kelvin: area × depth × ρ × cp / 3600 (D4 §4.3)."""
        return self.area_m2 * (self.screed_mm / 1000.0) * self.rho * self.cp / 3600.0

    def required_kwh(
        self, level_now: float | None, target: float, deadline: datetime | None, ctx: StoreCtx
    ) -> float | None:
        """KWh to bring the slab to `target` by `deadline` (§5.7)."""
        if level_now is None:
            return None
        bounded = min(target, self.max_c) if self.direction != "cool" else max(target, self.min_c)
        hours = hours_until(deadline, ctx.now)
        return self.capacity_kwh_per_unit() * _deficit(
            level_now, bounded, self.direction
        ) + _loss_kwh(self.loss_coeff_w_per_k, level_now, ctx, hours)

    def max_level(self) -> float:
        """Return the covering's maximum - 27 °C under wood, 30 °C over tile (INV-56)."""
        return self.max_c

    def min_level(self) -> float:
        """Return the frost guard."""
        return self.min_c

    def coast_hours(self, level_now: float, floor: float, ctx: StoreCtx) -> float | None:
        """How long the slab holds above `floor`, or `None` unfitted (§5.7)."""
        if self.loss_coeff_w_per_k is None or ctx.outdoor_c is None:
            return None
        inside = ctx.indoor_c if ctx.indoor_c is not None else level_now
        loss_kw = self.loss_coeff_w_per_k * abs(inside - ctx.outdoor_c) / 1000.0
        if loss_kw <= 0.0:
            return None
        return self.capacity_kwh_per_unit() * _deficit(floor, level_now, self.direction) / loss_kw


@dataclass(frozen=True, slots=True)
class RoomStore:
    """A room's air and the building mass around it (D4 §4.3, §5.7)."""

    volume_m3: float
    heat_loss_w_per_k: float | None
    thermal_mass_kwh_per_k: float
    max_c: float
    min_c: float
    direction: StoreDirection = "heat"

    @classmethod
    def from_volume(
        cls,
        *,
        volume_m3: float,
        max_c: float,
        min_c: float,
        heat_loss_w_per_k: float | None = None,
        direction: StoreDirection = "heat",
    ) -> RoomStore:
        """Build one with the default mass of light-weight construction (§5.7)."""
        return cls(
            volume_m3=volume_m3,
            heat_loss_w_per_k=heat_loss_w_per_k,
            thermal_mass_kwh_per_k=ROOM_MASS_KWH_PER_K_PER_M3 * volume_m3,
            max_c=max_c,
            min_c=min_c,
            direction=direction,
        )

    def capacity_kwh_per_unit(self) -> float:
        """KWh per kelvin of the room and its mass."""
        return self.thermal_mass_kwh_per_k

    def required_kwh(
        self, level_now: float | None, target: float, deadline: datetime | None, ctx: StoreCtx
    ) -> float | None:
        """KWh to bring the room to `target` by `deadline` (§5.7)."""
        if level_now is None:
            return None
        bounded = min(target, self.max_c) if self.direction != "cool" else max(target, self.min_c)
        hours = hours_until(deadline, ctx.now)
        return self.thermal_mass_kwh_per_k * _deficit(
            level_now, bounded, self.direction
        ) + _loss_kwh(self.heat_loss_w_per_k, level_now, ctx, hours)

    def max_level(self) -> float:
        """Return the room's cap (INV-56)."""
        return self.max_c

    def min_level(self) -> float:
        """Return the room's floor."""
        return self.min_c

    def coast_hours(self, level_now: float, floor: float, ctx: StoreCtx) -> float | None:
        """How long the room holds above `floor`, or `None` unfitted (§5.7)."""
        if self.heat_loss_w_per_k is None or ctx.outdoor_c is None:
            return None
        loss_kw = self.heat_loss_w_per_k * abs(level_now - ctx.outdoor_c) / 1000.0
        if loss_kw <= 0.0:
            return None
        return self.thermal_mass_kwh_per_k * _deficit(floor, level_now, self.direction) / loss_kw


@dataclass(frozen=True, slots=True)
class TankStore:
    """A hot-water cylinder (D4 §4.3, §5.7).

    The `water_heater` type, its legionella cycle (INV-54) and the sensorless
    model for a tank on a plug are WP3.3; the arithmetic is here because it is
    §4.3's and because D4 §9 13 pins it.
    """

    litres: float
    standby_loss_w: float
    max_c: float
    min_c: float
    eta: float = 0.98
    direction: StoreDirection = "heat"

    def capacity_kwh_per_unit(self) -> float:
        """KWh per kelvin of the whole tank, after the element's losses."""
        return self.litres * WATER_KJ_PER_LK / 3600.0 / self.eta

    def required_kwh(
        self, level_now: float | None, target: float, deadline: datetime | None, ctx: StoreCtx
    ) -> float | None:
        """KWh to reach `target` by `deadline`, standby loss included (§5.7)."""
        if level_now is None:
            return None
        bounded = min(target, self.max_c)
        hours = hours_until(deadline, ctx.now)
        heat = self.capacity_kwh_per_unit() * _deficit(level_now, bounded, self.direction)
        return heat + self.standby_loss_w * hours / 1000.0

    def max_level(self) -> float:
        """Return the thermostat's maximum (INV-56)."""
        return self.max_c

    def min_level(self) -> float:
        """Return the comfort minimum - 45 °C by default, and never a legionella nursery."""
        return self.min_c

    def coast_hours(self, level_now: float, floor: float, ctx: StoreCtx) -> float | None:
        """How long the tank holds above `floor` on standby loss alone (§5.7)."""
        if self.standby_loss_w <= 0.0:
            return None
        return (
            self.capacity_kwh_per_unit()
            * _deficit(floor, level_now, self.direction)
            / (self.standby_loss_w / 1000.0)
        )
