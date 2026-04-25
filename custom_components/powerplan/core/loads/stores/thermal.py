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

A tank on a plug with no temperature sensor gets a `SensorlessModel` (D4 §5.7):
energy in, minus standby loss, minus the household's draw-off, **re-anchored to
the thermostat's own setting the moment the element is seen to stop drawing**.
That anchor is what keeps an integrator from drifting for a week, and it exists
because INV-64 refuses a relay-controlled tank that has no mechanical thermostat
in the first place (D4 §6.3) - so there is always a dial to anchor on.
"""

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Final

from ...model import Confidence
from .base import StoreCtx, StoreDirection, hours_until

__all__ = [
    "CP_SCREED",
    "DRAW_L_PER_PERSON_DAY",
    "DRAW_TEMP_C",
    "RHO_SCREED",
    "WATER_KJ_PER_LK",
    "DrawOffProfile",
    "RoomStore",
    "SensorlessEstimate",
    "SensorlessModel",
    "SlabStore",
    "TankStore",
]

#: Screed: density kg/m³ and specific heat kJ/kgK (D4 §4.3).
RHO_SCREED = 2200.0
CP_SCREED = 0.9

#: Water's specific heat, kJ per litre-kelvin.
WATER_KJ_PER_LK = 4.186

#: The effective building mass of light-weight (timber) construction, kWh/K per
#: m³ (D4 §5.7). The air alone would be 0.0003; heavy masonry is 2–3× this.
ROOM_MASS_KWH_PER_K_PER_M3 = 0.03

#: Domestic hot water per person per day, litres at `DRAW_TEMP_C` (D4 §5.7, §6.3).
DRAW_L_PER_PERSON_DAY: Final = 45.0

#: The temperature the draw-off estimate is quoted at (D4 §5.7).
DRAW_TEMP_C: Final = 55.0

#: Incoming mains water. 8 °C is the Nordic year-round mean of a buried supply;
#: it is what the energy in a litre of stored hot water is measured against.
COLD_WATER_C: Final = 8.0


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
    sensorless: SensorlessModel | None = None

    def capacity_kwh_per_unit(self) -> float:
        """KWh per kelvin of the whole tank, after the element's losses."""
        return self.litres * WATER_KJ_PER_LK / 3600.0 / self.eta

    def stored_kwh_per_k(self) -> float:
        """KWh **in the water** per kelvin - the element's losses not counted.

        `capacity_kwh_per_unit()` is what the plug must deliver per kelvin; this
        is what the water holds, and it is the one the sensorless integrator
        divides by (the η belongs on the way in, once).
        """
        return self.litres * WATER_KJ_PER_LK / 3600.0

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


# --------------------------------------------------------------------------- #
# A tank with no temperature sensor (D4 §5.7)
# --------------------------------------------------------------------------- #


def _hour_of(local: datetime) -> float:
    """Hours since local midnight, fractional."""
    return local.hour + local.minute / 60.0 + local.second / 3600.0


@dataclass(frozen=True, slots=True)
class DrawOffProfile:
    """How much hot water a household takes, and when (D4 §5.7, §6.3).

    `persons × 45 L/day at 55 °C`, weighted to morning and evening, because that
    is the shape that decides whether a tank charged at 03:00 survives to the
    second shower. The windows are **local** wall-clock statements - a household
    showers by the clock, not by UTC - which is the same licence D4 §5.8 gives a
    weekly schedule (HLD §7.1).
    """

    persons: int
    litres_per_person_day: float = DRAW_L_PER_PERSON_DAY
    draw_temp_c: float = DRAW_TEMP_C
    cold_water_c: float = COLD_WATER_C
    morning: tuple[float, float] = (6.0, 9.0)
    daytime: tuple[float, float] = (9.0, 17.0)
    evening: tuple[float, float] = (17.0, 22.0)
    morning_share: float = 0.35
    evening_share: float = 0.45

    @property
    def litres_per_day(self) -> float:
        """Litres at `draw_temp_c` the household takes in a day."""
        return self.persons * self.litres_per_person_day

    @property
    def daytime_share(self) -> float:
        """Whatever the morning and the evening left - 20 % by default."""
        return max(0.0, 1.0 - self.morning_share - self.evening_share)

    @property
    def kwh_per_day(self) -> float:
        """The heat the draws take out of the tank in a day."""
        return (
            self.litres_per_day * WATER_KJ_PER_LK * (self.draw_temp_c - self.cold_water_c) / 3600.0
        )

    def _windows(self) -> tuple[tuple[float, float, float], ...]:
        return (
            (*self.morning, self.morning_share),
            (*self.daytime, self.daytime_share),
            (*self.evening, self.evening_share),
        )

    def fraction_between_hours(self, from_h: float, to_h: float) -> float:
        """Return the share of a day's litres drawn between two local hours.

        Piecewise-constant inside each window and zero outside them: nobody
        showers at 03:00, which is exactly why the slot at 03:00 is the one the
        planner wants to charge in.
        """
        total = 0.0
        for start, end, share in self._windows():
            if end <= start or share <= 0.0:
                continue
            overlap = max(0.0, min(to_h, end) - max(from_h, start))
            total += share * overlap / (end - start)
        return total

    def litres_between(self, from_: datetime, until: datetime, zone: tzinfo) -> float:
        """Litres at `draw_temp_c` drawn in `[from_, until)`, in `zone`'s day."""
        if until <= from_:
            return 0.0
        start, end = from_.astimezone(zone), until.astimezone(zone)
        if start.date() == end.date():
            return self.litres_per_day * self.fraction_between_hours(_hour_of(start), _hour_of(end))
        whole_days = (end.date() - start.date()).days - 1
        full = self.fraction_between_hours(0.0, 24.0)
        return self.litres_per_day * (
            self.fraction_between_hours(_hour_of(start), 24.0)
            + whole_days * full
            + self.fraction_between_hours(0.0, _hour_of(end))
        )

    def kwh_between(self, from_: datetime, until: datetime, zone: tzinfo) -> float:
        """Return the heat those litres take with them, kWh."""
        litres = self.litres_between(from_, until, zone)
        return litres * WATER_KJ_PER_LK * (self.draw_temp_c - self.cold_water_c) / 3600.0


@dataclass(frozen=True, slots=True)
class SensorlessEstimate:
    """What the sensorless model believes, and how much that is worth (D4 §5.7)."""

    temp_c: float
    at: datetime
    confidence: Confidence
    anchored: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SensorlessModel:
    """A tank on a plug with no thermometer (D4 §5.7).

    Energy in (measured), minus standby loss, minus the draw-off profile, over
    the water's own heat capacity - and **re-anchored to the thermostat's own
    setting whenever the element is seen to stop drawing while it is powered**.
    An integrator alone would drift a kelvin a day and nobody would know; the
    anchor is a free measurement the hardware makes for us several times a day.

    The estimate starts at the tank's **floor**, not at the dial: the error of
    assuming a cold tank is one heat-up at whatever the price happens to be, and
    the error of assuming a hot one is a cold shower.
    """

    litres: float
    standby_loss_w: float
    anchor_c: float
    floor_c: float
    draw: DrawOffProfile
    eta: float = 0.98
    cold_water_c: float = COLD_WATER_C
    #: Under this the element is not drawing: a smart plug's own electronics and
    #: the measurement noise of a clamp are both well inside 25 W.
    idle_w: float = 25.0

    def stored_kwh_per_k(self) -> float:
        """KWh in the water per kelvin."""
        return self.litres * WATER_KJ_PER_LK / 3600.0

    def initial(self, at: datetime) -> SensorlessEstimate:
        """Return the first estimate: the floor, pessimistically (§5.7)."""
        return SensorlessEstimate(
            temp_c=self.floor_c,
            at=at,
            confidence=Confidence.ESTIMATED,
            reason="no history: a tank of unknown temperature is assumed at its floor",
        )

    def advance(
        self,
        previous: SensorlessEstimate | None,
        *,
        at: datetime,
        measured_w: float | None,
        powered: bool,
        zone: tzinfo,
    ) -> SensorlessEstimate:
        """Move the estimate to `at` from what the plug measured (§5.7).

        `powered` is whether *we* are letting the element have mains: the anchor
        is only a measurement when the answer is yes. A plug we switched off that
        draws nothing says nothing about the water.
        """
        if previous is None:
            return self.initial(at)
        dt_s = max(0.0, (at - previous.at).total_seconds())
        if dt_s == 0.0:
            return previous

        if powered and measured_w is not None and measured_w <= self.idle_w:
            return SensorlessEstimate(
                temp_c=self.anchor_c,
                at=at,
                confidence=Confidence.ESTIMATED,
                anchored=True,
                reason=f"the thermostat stopped drawing: the tank is at {self.anchor_c:.0f} °C",
            )

        in_kwh = 0.0 if measured_w is None else max(0.0, measured_w) * dt_s / 3_600_000.0
        loss_kwh = self.standby_loss_w * dt_s / 3_600_000.0
        draw_kwh = self.draw.kwh_between(previous.at, at, zone)
        delta_k = (in_kwh * self.eta - loss_kwh - draw_kwh) / self.stored_kwh_per_k()
        temp_c = min(self.anchor_c, max(self.cold_water_c, previous.temp_c + delta_k))
        blind = measured_w is None
        return SensorlessEstimate(
            temp_c=temp_c,
            at=at,
            confidence=Confidence.STALE if blind else Confidence.ESTIMATED,
            reason="no power reading: loss and draw-off only" if blind else "integrated",
        )
