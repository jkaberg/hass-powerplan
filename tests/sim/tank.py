"""A 300 L water heater: two stratified layers, 3 kW element, real draw-off.

`TankStore` (D4 §4.3, §5.7) treats the tank as one lumped mass.  This simulator
splits it into a hot upper layer and a cold lower layer that exchange heat by
conduction and, when the element makes the bottom warmer than the top, by
buoyancy.  That is the difference the planner has to survive: a tank whose
thermostat sensor sits low reheats while the top is still hot, and a tank whose
top has been drawn cannot deliver a 55 °C shower however good its average is.

Draw-off is discrete, not a smear: showers and taps at seeded times, 45 L per
person per day at 55 °C, weighted to morning and evening (D4 §5.7).  A draw the
tank cannot serve at 55 °C is counted in `comfort_short_l` - the number the
benchmark's `comfort_violation_min` is built from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .base import TEMP_BOTTOM, TEMP_TOP, Command, Env, Reads, amps_1p, derive_rng, kwh

WATER_CP_KJ_PER_KG_K = 4.186
DEFAULT_LITRES = 300.0
ELEMENT_W = 3000.0
ETA = 0.98
STANDBY_LOSS_W = 60.0
STANDBY_DELTA_K = 55.0
AMBIENT_C = 20.0
DEFAULT_SETPOINT_C = 75.0
MAX_C = 80.0
COMFORT_MIN_C = 45.0
THERMOSTAT_HYSTERESIS_K = 5.0
LEGIONELLA_C = 65.0
LEGIONELLA_HOLD_S = 3600.0
LEGIONELLA_BAND_K = 1.0
TOP_FRACTION = 0.5
MIX_UA_W_PER_K = 15.0
DRAW_L_PER_PERSON_DAY = 45.0
DRAW_TEMP_C = 55.0
SHOWER_L = 40.0
SHOWER_S = 480.0
TAP_L = 5.0
TAP_S = 60.0
MORNING_SHARE = 0.35
EVENING_SHARE = 0.45
MORNING_FROM_H = 6
MORNING_TO_H = 9
EVENING_FROM_H = 17
EVENING_TO_H = 22
PARTIAL_DRAW_FLOOR = 0.1
SENSOR_STEP_K = 0.1
KJ_PER_KWH = 3600.0

SOURCES: dict[str, str] = {
    "WATER_CP_KJ_PER_KG_K": "D4 §4.3 TankStore (kWh = litres × 4.186 × ΔK / 3600 / eta)",
    "DEFAULT_LITRES": "D9 §5.9 house spec (300 L); D4 §6.3 tank-size options",
    "ELEMENT_W": "D9 §5.9 house spec (3 kW); D4 §6.3 element options",
    "ETA": "D4 §4.3 TankStore eta 0.98",
    "STANDBY_LOSS_W": "D4 §6.3 advanced default: standby loss 60 W",
    "STANDBY_DELTA_K": (
        "assumed: the 60 W figure is quoted at roughly 75 °C water in a 20 °C room, i.e. "
        "ΔT ≈ 55 K, so UA ≈ 1.09 W/K. Replaced by a measured standing-loss curve"
    ),
    "AMBIENT_C": "assumed: the tank stands in a heated room at 20 °C (D4 §6.4 comfort default 21)",
    "DEFAULT_SETPOINT_C": "D4 §5.12: deadline temperature default 75 °C",
    "MAX_C": "D4 §6.3 advanced: max 80 °C",
    "COMFORT_MIN_C": "D4 §5.12: comfort floor 45 °C (below ~50 °C storage favours legionella)",
    "THERMOSTAT_HYSTERESIS_K": (
        "assumed: 5 K differential on a tank thermostat (mechanical types are 5–8 K). "
        "Replaced by the measured reheat band of the reference house's tank"
    ),
    "LEGIONELLA_C": "D4 §5.12 / §6.3: legionella cycle 65 °C",
    "LEGIONELLA_HOLD_S": "D4 §5.12: hold 60 min",
    "LEGIONELLA_BAND_K": "D4 §5.12: 'temp ≥ legionella_temp − 1 K for hold_min'",
    "TOP_FRACTION": (
        "assumed: the thermocline sits mid-tank, so the two layers are half the volume each. "
        "Replaced by a measured two-sensor profile"
    ),
    "MIX_UA_W_PER_K": (
        "assumed: 15 W/K conduction across the thermocline — slow enough that stratification "
        "survives an evening, fast enough that a tank left alone becomes uniform overnight"
    ),
    "DRAW_L_PER_PERSON_DAY": "D4 §5.7 / §6.3: 45 L per person per day at 55 °C",
    "DRAW_TEMP_C": "D4 §5.7: the draw-off estimate is quoted at 55 °C",
    "SHOWER_L": "assumed: an 8-minute shower at ~5 L/min of 55 °C water",
    "SHOWER_S": "assumed: 8 minutes",
    "TAP_L": "assumed: a hand-wash or washing-up draw",
    "TAP_S": "assumed: 1 minute",
    "MORNING_SHARE": "assumed: 35 % of the day's hot water 06–09 (D4 §5.7 'morning/evening weighted')",
    "EVENING_SHARE": "assumed: 45 % of the day's hot water 17–22 (D4 §5.7)",
    "MORNING_FROM_H": "assumed: the household's morning window (sim/household.py departs 07:30)",
    "MORNING_TO_H": "assumed: the morning hot-water window ends at 09:00",
    "EVENING_FROM_H": "assumed: the household's evening window (arrival 16:30)",
    "EVENING_TO_H": "assumed: the evening hot-water window ends at 22:00",
    "PARTIAL_DRAW_FLOOR": (
        "assumed: a residue below 10 % of a unit draw is not worth a separate tap, so the day's "
        "total lands within 10 % of one tap of the 45 L/person figure"
    ),
    "SENSOR_STEP_K": "a tank temperature sensor in HA reports 0.1 K steps",
    "KJ_PER_KWH": "SI",
}


@dataclass(frozen=True, slots=True)
class Draw:
    """One hot-water draw: `litres` at `DRAW_TEMP_C`, starting at `start`."""

    start: datetime
    seconds: float
    litres_at_55: float


@dataclass(slots=True)
class DrawProfile:
    """Discrete hot-water draws for a household, deterministic per local day."""

    persons: int
    seed: int
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))
    _cache: dict[int, tuple[Draw, ...]] = field(default_factory=dict)

    def day(self, t: datetime) -> tuple[Draw, ...]:
        """Every draw of the local day containing `t`."""
        local = t.astimezone(self.tz)
        ordinal = local.date().toordinal()
        cached = self._cache.get(ordinal)
        if cached is not None:
            return cached
        rng = derive_rng(self.seed, "draw", ordinal)
        budget = DRAW_L_PER_PERSON_DAY * self.persons
        draws: list[Draw] = []

        def add(hour_from: int, hour_to: int, litres: float, unit: float, unit_s: float) -> None:
            remaining = litres
            while remaining > unit * PARTIAL_DRAW_FLOOR:
                take = min(unit, remaining)
                hour = rng.uniform(hour_from, hour_to)
                start = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                    hours=hour
                )
                draws.append(Draw(start.astimezone(t.tzinfo), unit_s * take / unit, take))
                remaining -= take

        add(MORNING_FROM_H, MORNING_TO_H, budget * MORNING_SHARE, SHOWER_L, SHOWER_S)
        add(EVENING_FROM_H, EVENING_TO_H, budget * EVENING_SHARE, SHOWER_L, SHOWER_S)
        daytime = budget * (1.0 - MORNING_SHARE - EVENING_SHARE)
        add(MORNING_TO_H, EVENING_FROM_H, daytime, TAP_L, TAP_S)
        out = tuple(sorted(draws, key=lambda d: d.start))
        self._cache[ordinal] = out
        return out

    def litres_at_55(self, t0: datetime, t1: datetime) -> float:
        """Litres at 55 °C demanded in [`t0`, `t1`) - the two adjacent days suffice."""
        total = 0.0
        for anchor in (t0 - timedelta(days=1), t0, t1):
            for d in self.day(anchor):
                end = d.start + timedelta(seconds=d.seconds)
                overlap = (min(t1, end) - max(t0, d.start)).total_seconds()
                if overlap > 0.0:
                    total += d.litres_at_55 * overlap / d.seconds
        return total


@dataclass(slots=True)
class TankSim:
    """A stratified two-layer tank with an element, a thermostat and draw-off."""

    litres: float = DEFAULT_LITRES
    element_w: float = ELEMENT_W
    setpoint_c: float = DEFAULT_SETPOINT_C
    hysteresis_k: float = THERMOSTAT_HYSTERESIS_K
    draw: DrawProfile | None = None
    top_c: float = 60.0
    bottom_c: float = 50.0
    element_on: bool = False
    plug_on: bool = True
    energy_in_kwh: float = 0.0
    loss_kwh: float = 0.0
    draw_kwh: float = 0.0
    comfort_short_l: float = 0.0
    legionella_hold_s: float = 0.0
    legionella_cycles: int = 0

    # -- geometry ----------------------------------------------------------- #

    @property
    def mass_top_kg(self) -> float:
        """Mass of the upper layer, kg."""
        return self.litres * TOP_FRACTION

    @property
    def mass_bottom_kg(self) -> float:
        """Mass of the lower layer, kg."""
        return self.litres * (1.0 - TOP_FRACTION)

    @property
    def stored_kwh(self) -> float:
        """Energy in the water above 0 °C."""
        e_kj = WATER_CP_KJ_PER_KG_K * (
            self.mass_top_kg * self.top_c + self.mass_bottom_kg * self.bottom_c
        )
        return e_kj / KJ_PER_KWH

    @property
    def sensor_c(self) -> float:
        """What the thermostat sees - low in the tank, so it sees the cold layer."""
        return self.bottom_c

    # -- physics ------------------------------------------------------------ #

    def _serve_draw(self, dt_s: float, env: Env) -> None:
        if self.draw is None:
            return
        litres_55 = self.draw.litres_at_55(env.now, env.now + timedelta(seconds=dt_s))
        if litres_55 <= 0.0:
            return
        cold = env.cold_water_c
        if self.top_c <= DRAW_TEMP_C:
            # Nothing to mix with: the tank gives what it has and the shower is short.
            litres_tank = litres_55
            if self.top_c > cold:
                self.comfort_short_l += (
                    litres_55 * (DRAW_TEMP_C - self.top_c) / (DRAW_TEMP_C - cold)
                )
            else:
                self.comfort_short_l += litres_55
        else:
            litres_tank = litres_55 * (DRAW_TEMP_C - cold) / (self.top_c - cold)
        litres_tank = min(litres_tank, self.mass_top_kg)

        self.draw_kwh += (litres_tank * WATER_CP_KJ_PER_KG_K * (self.top_c - cold)) / KJ_PER_KWH
        new_top = self.top_c + litres_tank * (self.bottom_c - self.top_c) / self.mass_top_kg
        new_bottom = self.bottom_c + litres_tank * (cold - self.bottom_c) / self.mass_bottom_kg
        self.top_c, self.bottom_c = new_top, new_bottom

    def _thermostat(self) -> None:
        if self.element_on:
            self.element_on = self.sensor_c < min(self.setpoint_c, MAX_C)
        else:
            self.element_on = self.sensor_c < min(self.setpoint_c, MAX_C) - self.hysteresis_k

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Serve the draws, run the element, conduct, mix and lose heat."""
        if command is not None:
            if command.setpoint_c is not None:
                self.setpoint_c = min(command.setpoint_c, MAX_C)
            if command.on is not None:
                self.plug_on = command.on

        self._serve_draw(dt_s, env)
        self._thermostat()
        power_w = self.element_w if (self.element_on and self.plug_on) else 0.0

        ua_standby = STANDBY_LOSS_W / STANDBY_DELTA_K
        q_loss_top = ua_standby * TOP_FRACTION * (self.top_c - AMBIENT_C)
        q_loss_bottom = ua_standby * (1.0 - TOP_FRACTION) * (self.bottom_c - AMBIENT_C)
        q_conduct = MIX_UA_W_PER_K * (self.top_c - self.bottom_c)

        c_top = self.mass_top_kg * WATER_CP_KJ_PER_KG_K * 1000.0
        c_bottom = self.mass_bottom_kg * WATER_CP_KJ_PER_KG_K * 1000.0
        self.top_c += (-q_loss_top - q_conduct) * dt_s / c_top
        self.bottom_c += (power_w * ETA + q_conduct - q_loss_bottom) * dt_s / c_bottom

        # Buoyancy: a bottom warmer than the top cannot stay there.
        if self.bottom_c > self.top_c:
            mean = (
                self.mass_top_kg * self.top_c + self.mass_bottom_kg * self.bottom_c
            ) / self.litres
            self.top_c = self.bottom_c = mean

        self.energy_in_kwh += kwh(power_w, dt_s)
        self.loss_kwh += kwh(q_loss_top + q_loss_bottom, dt_s)

        if min(self.top_c, self.bottom_c) >= LEGIONELLA_C - LEGIONELLA_BAND_K:
            self.legionella_hold_s += dt_s
            if self.legionella_hold_s >= LEGIONELLA_HOLD_S:
                self.legionella_cycles += 1
                self.legionella_hold_s = 0.0
        else:
            self.legionella_hold_s = 0.0

        return Reads(
            power_w=power_w,
            amps=amps_1p(power_w),
            status="heating" if power_w > 0.0 else "idle",
            values={
                TEMP_TOP: round(self.top_c / SENSOR_STEP_K) * SENSOR_STEP_K,
                TEMP_BOTTOM: round(self.bottom_c / SENSOR_STEP_K) * SENSOR_STEP_K,
            },
        )
