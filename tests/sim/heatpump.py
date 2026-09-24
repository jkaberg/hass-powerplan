"""An air-to-air heat pump: a COP curve with a mechanism, and real defrost.

D4 §6.4 gives the product a COP curve as six interpolated points.  This
simulator does not use that table as its model - it uses a *mechanism*: Carnot
efficiency against the indoor supply temperature, multiplied by an exergy
efficiency that itself falls as the outdoor air gets colder.  The mechanism is
anchored on two of D4's points (+7 °C and −15 °C) and therefore disagrees with
the product's straight lines everywhere between them, which is exactly what
D9 §2 asks for: the planner meets a house it does not already assume.

Defrost is modelled with its full signature, because that signature is what
D4 §5.14 detects:

1. the reversing valve swings - fans stop, the compressor idles, and the power
   **dips** to standby for a few seconds;
2. the compressor then runs hard while the indoor outlet air goes *cold*: power
   up while the outlet temperature falls, which is the detector;
3. a recovery period at full power after the ice is gone.

A controller that sheds on step 2 is shedding a unit that is already not
heating the house, and it will do it again forty-five minutes later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import (
    COP,
    SETPOINT_C,
    TEMP_AIR,
    TEMP_OUTLET,
    Command,
    Env,
    Reads,
    amps_1p,
    kwh,
)

RATED_W = 1500.0
STANDBY_W = 25.0
MIN_MODULATION = 0.15
SUPPLY_AIR_C = 35.0
KELVIN_0C = 273.15
COP_ANCHOR_PLUS7 = 3.8
COP_ANCHOR_MINUS15 = 1.8
COP_MAX = 5.5
COP_MIN = 1.0
BAND_K = 1.0
DWELL_S = 1800.0
DEFROST_BELOW_C = 3.0
DEFROST_INTERVAL_S = 2700.0
DEFROST_DRY_BELOW_C = -10.0
DEFROST_DRY_FACTOR = 2.0
DEFROST_S = 300.0
DEFROST_VALVE_S = 20.0
DEFROST_RECOVERY_S = 120.0
DEFROST_OUTLET_DROP_K = 5.0
DEFROST_HEAT_FRACTION = 0.5
CARNOT_MIN_LIFT_K = 5.0
OUTLET_RISE_K_PER_KW = 12.0
ROOM_HEIGHT_M = 2.5
ROOM_MASS_KWH_PER_K_PER_M3 = 0.03
DEFAULT_U_ENVELOPE_W_PER_M2K = 0.7
SENSOR_STEP_K = 0.1
J_PER_KWH = 3_600_000.0
#: Cooling: the indoor coil's air, the outdoor coil's lift above ambient,
#: and the rated EER point the exergy efficiency is anchored on.
EVAPORATOR_AIR_C = 12.0
CONDENSER_LIFT_K = 10.0
EER_ANCHOR_35 = 3.2

SOURCES: dict[str, str] = {
    "RATED_W": "D9 §5.9 house spec: air-to-air 1.5 kW rated; D4 §6.4 asks for it always",
    "STANDBY_W": (
        "D4 §5.14 'an inverter at 23 W does not reserve 3 kW' – 25 W standby/electronics, "
        "rounded from that observation"
    ),
    "MIN_MODULATION": (
        "assumed: an inverter compressor's minimum is ~15 % of rated. Replaced by the "
        "measured minimum draw of the reference house's unit"
    ),
    "SUPPLY_AIR_C": (
        "assumed: 35 °C indoor outlet air in heating, the hot-side temperature the Carnot "
        "term needs. Replaced by a captured outlet sensor (D4 §6.4 advanced names one)"
    ),
    "KELVIN_0C": "SI",
    "COP_ANCHOR_PLUS7": "D4 §6.4 A2A default curve, point 7 °C → 3.8 (effektstyring's curve)",
    "COP_ANCHOR_MINUS15": "D4 §6.4 A2A default curve, point −15 °C → 1.8",
    "COP_MAX": (
        "Toshiba Daiseikai 10 headline figure, SCOP up to 5.5 "
        "(https://toshiba-aircondition.com/en/energy-efficiency.html); assumed as the cap on a "
        "point COP because no manufacturer point COP above +7 °C was found in the lookup budget"
    ),
    "COP_MIN": "thermodynamics: a heat pump below COP 1 is a resistive heater",
    "BAND_K": "D9 §5.9 house spec and D4 §6.4 advanced: band ±1 K",
    "DWELL_S": "D4 §6.4 advanced: dwell 1800 s",
    "DEFROST_BELOW_C": "D9 §5.9 house spec: defrost cycles below +3 °C",
    "DEFROST_INTERVAL_S": (
        "assumed: 45 min between defrosts in the humid band around 0 °C. Replaced by the "
        "interval measured from the reference house's power trace"
    ),
    "DEFROST_DRY_BELOW_C": (
        "assumed: below −10 °C the air carries little moisture, so frosting slows"
    ),
    "DEFROST_DRY_FACTOR": "assumed: the interval roughly doubles in dry cold",
    "DEFROST_S": "assumed: a 5-minute reverse-cycle defrost. Replaced by a measured cycle",
    "DEFROST_VALVE_S": (
        "assumed: 20 s for the four-way valve to swing with the fans stopped – the power dip"
    ),
    "DEFROST_RECOVERY_S": "assumed: 2 min at full power after the ice is gone",
    "DEFROST_OUTLET_DROP_K": (
        "assumed: the indoor outlet air runs ~5 K below room temperature during defrost – the "
        "'power up while outlet falls' signature of D4 §5.14"
    ),
    "DEFROST_HEAT_FRACTION": (
        "assumed: half the compressor's output goes into melting the outdoor coil and the "
        "indoor unit takes the rest out of the room, so the room is actively cooled"
    ),
    "CARNOT_MIN_LIFT_K": (
        "assumed: a 5 K guard so the Carnot term stays finite when the outdoor air approaches "
        "the supply temperature (the unit would be off in that weather anyway)"
    ),
    "OUTLET_RISE_K_PER_KW": (
        "assumed: 12 K of outlet-air rise per kW of delivered heat at nominal fan speed"
    ),
    "ROOM_HEIGHT_M": "D4 §6.4 ('heated area × 2.5 m' gives RoomStore volume)",
    "ROOM_MASS_KWH_PER_K_PER_M3": "D4 §5.7 RoomStore default (0.03 kWh/K per m³, timber)",
    "DEFAULT_U_ENVELOPE_W_PER_M2K": "D4 §6.4 building-age table, 2000–2010 → 0.7 W/m²·K",
    "SENSOR_STEP_K": "a climate entity reports temperature in 0.1 K steps",
    "J_PER_KWH": "SI",
    "EVAPORATOR_AIR_C": (
        "assumed: 12 °C air off the indoor coil in cooling, the usual design point for a split "
        "unit's supply air (ASHRAE Handbook, HVAC Systems, 'cooling coil leaving air 10–13 °C')"
    ),
    "CONDENSER_LIFT_K": "assumed: the outdoor coil condenses about 10 K above the outdoor air",
    "EER_ANCHOR_35": (
        "EN 14511 rating point for air-to-air cooling, 35 °C outdoor / 27 °C indoor; 3.2 is a "
        "typical small split unit's EER (Energy Label A band, Regulation (EU) 626/2011)"
    ),
}


def _carnot(outdoor_c: float) -> float:
    """Ideal COP between the outdoor air and the indoor supply air."""
    hot = SUPPLY_AIR_C + KELVIN_0C
    cold = min(outdoor_c, SUPPLY_AIR_C - CARNOT_MIN_LIFT_K) + KELVIN_0C
    return hot / (hot - cold)


def _carnot_cooling(outdoor_c: float) -> float:
    """Ideal cooling COP between the indoor coil air and the outdoor coil."""
    cold = EVAPORATOR_AIR_C + KELVIN_0C
    hot = max(outdoor_c + CONDENSER_LIFT_K, EVAPORATOR_AIR_C + CARNOT_MIN_LIFT_K) + KELVIN_0C
    return cold / (hot - cold)


def cooling_cop_at(outdoor_c: float) -> float:
    """Cooling COP: the exergy efficiency at the EN 14511 point, times Carnot."""
    eta = EER_ANCHOR_35 / _carnot_cooling(35.0)
    return max(COP_MIN, min(COP_MAX, eta * _carnot_cooling(outdoor_c)))


def cop_at(outdoor_c: float) -> float:
    """COP as exergy efficiency × Carnot, anchored on D4 §6.4's +7 and −15 points."""
    eta_7 = COP_ANCHOR_PLUS7 / _carnot(7.0)
    eta_m15 = COP_ANCHOR_MINUS15 / _carnot(-15.0)
    slope = (eta_7 - eta_m15) / (7.0 - -15.0)
    eta = eta_m15 + slope * (outdoor_c - -15.0)
    return max(COP_MIN, min(COP_MAX, eta * _carnot(outdoor_c)))


@dataclass(slots=True)
class HeatPumpSim:
    """An inverter air-to-air unit heating one room, with defrost cycles."""

    area_m2: float
    rated_w: float = RATED_W
    u_envelope_w_per_m2k: float = DEFAULT_U_ENVELOPE_W_PER_M2K
    setpoint_c: float = 21.0
    band_k: float = BAND_K
    room_c: float = 21.0
    hvac_on: bool = True
    #: `heat` or `cool`: which way the unit moves the room.
    mode: str = "heat"
    energy_in_kwh: float = 0.0
    heat_out_kwh: float = 0.0
    loss_kwh: float = 0.0
    defrost_count: int = 0
    _compressor_s: float = field(default=0.0)
    _since_defrost_s: float = field(default=0.0)
    _defrost_s_left: float = field(default=0.0)
    _recovery_s_left: float = field(default=0.0)
    _dwell_s_left: float = field(default=0.0)

    @property
    def kwh_per_k(self) -> float:
        """Room heat capacity, kWh/K."""
        return self.area_m2 * ROOM_HEIGHT_M * ROOM_MASS_KWH_PER_K_PER_M3

    @property
    def stored_kwh(self) -> float:
        """Energy in the room node above 0 °C."""
        return self.kwh_per_k * self.room_c

    @property
    def defrosting(self) -> bool:
        """True while a defrost cycle is in progress."""
        return self._defrost_s_left > 0.0

    def _modulation(self, dt_s: float) -> float:
        """Proportional band control with a dwell, as D4 §5.4/§6.4 describes."""
        self._dwell_s_left = max(0.0, self._dwell_s_left - dt_s)
        if not self.hvac_on:
            return 0.0
        error = self.setpoint_c - self.room_c
        if self.mode == "cool":
            error = -error
        fraction = max(0.0, min(1.0, error / self.band_k))
        if fraction < MIN_MODULATION:
            if self._compressor_s > 0.0 and self._dwell_s_left > 0.0:
                return MIN_MODULATION
            self._compressor_s = 0.0
            return 0.0
        if self._compressor_s == 0.0:
            self._dwell_s_left = DWELL_S
        return fraction

    def _defrost_interval_s(self, outdoor_c: float) -> float:
        if outdoor_c < DEFROST_DRY_BELOW_C:
            return DEFROST_INTERVAL_S * DEFROST_DRY_FACTOR
        return DEFROST_INTERVAL_S

    def _apply(self, command: Command) -> None:
        """Take a SETPOINT or MODE write (D4 §5.4, §5.5)."""
        if command.setpoint_c is not None:
            self.setpoint_c = command.setpoint_c
        if command.mode is not None:
            self.hvac_on = command.mode != "off"
        if command.on is not None:
            self.hvac_on = command.on

    def _power_and_heat(self, dt_s: float, fraction: float, cop: float) -> tuple[float, float]:
        """Electrical draw and heat into the room, defrost signature included."""
        running = fraction > 0.0
        if self.defrosting:
            in_valve_swing = self._defrost_s_left > DEFROST_S - DEFROST_VALVE_S
            self._defrost_s_left -= dt_s
            if self._defrost_s_left <= 0.0:
                self._recovery_s_left = DEFROST_RECOVERY_S
            if in_valve_swing:
                return STANDBY_W, 0.0
            return self.rated_w, -self.rated_w * DEFROST_HEAT_FRACTION
        if self._recovery_s_left > 0.0 and running:
            self._recovery_s_left -= dt_s
            return self.rated_w, self.rated_w * cop
        if running:
            power_w = max(self.rated_w * fraction, self.rated_w * MIN_MODULATION)
            return power_w, power_w * cop
        return STANDBY_W, 0.0

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Modulate, defrost when the coil ices, heat the room and report."""
        if command is not None:
            self._apply(command)

        fraction = self._modulation(dt_s)
        running = fraction > 0.0
        if running:
            self._compressor_s += dt_s
            if env.outdoor_c < DEFROST_BELOW_C and self.mode != "cool":
                self._since_defrost_s += dt_s

        if not self.defrosting and self._since_defrost_s >= self._defrost_interval_s(env.outdoor_c):
            self._defrost_s_left = DEFROST_S
            self._since_defrost_s = 0.0
            self.defrost_count += 1

        cooling = self.mode == "cool"
        cop = cooling_cop_at(env.outdoor_c) if cooling else cop_at(env.outdoor_c)
        power_w, heat_w = self._power_and_heat(dt_s, fraction, cop)
        if cooling:
            # The same compressor work, taking heat out of the room.
            heat_w = -heat_w

        q_loss = self.u_envelope_w_per_m2k * self.area_m2 * (self.room_c - env.outdoor_c)
        self.room_c += (heat_w - q_loss) * dt_s / (self.kwh_per_k * J_PER_KWH)

        self.energy_in_kwh += kwh(power_w, dt_s)
        self.heat_out_kwh += kwh(heat_w, dt_s)
        self.loss_kwh += kwh(q_loss, dt_s)

        if self.defrosting:
            outlet_c = self.room_c - DEFROST_OUTLET_DROP_K
            status = "defrost"
        elif heat_w > 0.0:
            outlet_c = self.room_c + OUTLET_RISE_K_PER_KW * heat_w / 1000.0
            status = "heating"
        elif heat_w < 0.0 and cooling:
            outlet_c = EVAPORATOR_AIR_C
            status = "cooling"
        else:
            outlet_c = self.room_c
            status = "idle"

        return Reads(
            power_w=power_w,
            amps=amps_1p(power_w),
            status=status,
            values={
                TEMP_AIR: round(self.room_c / SENSOR_STEP_K) * SENSOR_STEP_K,
                TEMP_OUTLET: round(outlet_c / SENSOR_STEP_K) * SENSOR_STEP_K,
                SETPOINT_C: self.setpoint_c,
                COP: cop,
            },
        )
