"""Electric floor heating: a two-node RC slab and the room above it (D9 §2).

The product plans a floor loop with `SlabStore` - one lumped capacity, one
optional loss coefficient (D4 §4.3, §5.7).  This simulator is deliberately one
node richer: the screed and the room air are separate capacities coupled through
the floor surface, the room loses heat to outdoors and gains it from windows, and
the slab loses heat downwards to the ground.  A controller that assumes the
one-node model therefore meets a house that does not behave exactly like it -
which is the point (D9 §2, item 3).

The loop carries its own thermostat, because a Z-Wave floor thermostat does:
powerplan writes a setpoint or toggles Heat/Eco and the device switches the
cable (D4 §5.5).  The hardware floor minimum is honoured as a backstop that the
controller cannot override (HLD §7.8, INV-64).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import (
    SETPOINT_C,
    TEMP_AIR,
    TEMP_FLOOR,
    Command,
    Env,
    Reads,
    amps_1p,
    kwh,
)

# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #

SCREED_RHO = 2200.0
SCREED_CP_J_PER_KG_K = 900.0
DEFAULT_SCREED_MM = 50.0
DEFAULT_W_PER_M2 = 80.0
H_FLOOR_UP_W_PER_M2K = 10.8
U_GROUND_W_PER_M2K = 0.15
DEFAULT_U_ENVELOPE_W_PER_M2K = 0.7
ROOM_HEIGHT_M = 2.5
ROOM_MASS_KWH_PER_K_PER_M3 = 0.03
WINDOW_FRACTION = 0.15
WINDOW_SHGC = 0.5
SENSOR_TAU_S = 300.0
SENSOR_STEP_K = 0.1
COMMAND_LATENCY_S = 2.0
DEFAULT_SWING_K = 1.0
DEFAULT_MAX_C = 27.0
DEFAULT_FLOOR_MIN_C = 17.0
J_PER_KWH = 3_600_000.0

SOURCES: dict[str, str] = {
    "SCREED_RHO": "D4 §4.3 SlabStore (rho 2200 kg/m3); QA-slab-storage.md §1 uses the same",
    "SCREED_CP_J_PER_KG_K": "D4 §4.3 SlabStore (cp 0.9 kJ/kg·K) expressed in J/kg·K",
    "DEFAULT_SCREED_MM": (
        "QA-slab-storage.md §1 (reference house, 50 mm levelling compound); D4 §6.1 "
        "derives the product's store at 40 mm – the simulator is the thicker of the two "
        "on purpose (D9 §2)"
    ),
    "DEFAULT_W_PER_M2": (
        "D4 §6.1 'cable in screed 80 W/m²'; the reference house measures 103–105 W/m² in "
        "the bathrooms and 38–59 W/m² in the large rooms (QA-slab-storage.md §1)"
    ),
    "H_FLOOR_UP_W_PER_M2K": (
        "EN 1264-2: total heat transfer coefficient for upward heat flow from a heated "
        "floor surface to the room, 10.8 W/m²·K"
    ),
    "U_GROUND_W_PER_M2K": (
        "assumed: TEK17 §14-3 requires U ≤ 0.10 W/m²·K for a floor on ground; 0.15 adds "
        "the perimeter/edge loss a lumped U ignores. Replaced by a fitted coast rate from "
        "the reference house (QA-slab-storage.md §6 fit_coast_rate)"
    ),
    "DEFAULT_U_ENVELOPE_W_PER_M2K": "D4 §6.4 building-age table, 2000–2010 → 0.7 W/m²·K",
    "ROOM_HEIGHT_M": "D4 §6.4 ('heated area × 2.5 m' gives RoomStore volume)",
    "ROOM_MASS_KWH_PER_K_PER_M3": "D4 §5.7 RoomStore default (0.03 kWh/K per m³, timber)",
    "WINDOW_FRACTION": (
        "assumed: window area 15 % of floor area (TEK17 §13-7 caps it at 25 %). Replaced by "
        "the house spec's per-room glazing in WP0.11"
    ),
    "WINDOW_SHGC": "assumed: g ≈ 0.5 for Nordic triple glazing. Replaced by the house spec",
    "SENSOR_TAU_S": (
        "assumed: 5 min first-order lag for a sensor cast into the screed. Replaced by the "
        "step response of a captured Heatit floor sensor"
    ),
    "SENSOR_STEP_K": "Heatit Z-TRM reports floor and air temperature in 0.1 K steps",
    "COMMAND_LATENCY_S": (
        "assumed: 2 s for a Z-Wave setpoint or mode write to take effect (D9 §5.2 names "
        "Z-Wave latency as a quirk the simulator honours)"
    ),
    "DEFAULT_SWING_K": "D4 §6.1 advanced: swing 1.0 K (bathroom), 1.5 K elsewhere",
    "DEFAULT_MAX_C": "D4 §6.1: wood/parquet and laminate max 27 °C (EN 1264 surface limit)",
    "DEFAULT_FLOOR_MIN_C": "D4 §6.1: bedroom floor minimum 17 °C – the provisioned hardware floor",
    "J_PER_KWH": "SI",
}


@dataclass(slots=True)
class SlabSim:
    """A floor loop: screed node, room node, cable heater and a thermostat.

    `step` accepts a `Command` with `setpoint_c` and/or `mode` in
    `{"heat", "eco", "off"}`; the cable is switched by the device's own
    hysteresis, never by the command directly (D4 §5.5).
    """

    area_m2: float
    screed_mm: float = DEFAULT_SCREED_MM
    w_per_m2: float = DEFAULT_W_PER_M2
    u_envelope_w_per_m2k: float = DEFAULT_U_ENVELOPE_W_PER_M2K
    window_m2: float | None = None
    setpoint_c: float = 22.0
    eco_setpoint_c: float | None = None
    swing_k: float = DEFAULT_SWING_K
    max_c: float = DEFAULT_MAX_C
    floor_min_c: float = DEFAULT_FLOOR_MIN_C
    mode: str = "heat"
    screed_c: float = 22.0
    room_c: float = 21.0
    relay_on: bool = False
    energy_in_kwh: float = 0.0
    solar_in_kwh: float = 0.0
    loss_kwh: float = 0.0
    _sensor_c: float = field(default=-999.0)
    _pending: Command | None = field(default=None)
    _pending_in_s: float = field(default=0.0)

    def __post_init__(self) -> None:
        """Anchor the lagged sensor and the eco setpoint on the initial state."""
        if self._sensor_c < -900.0:
            self._sensor_c = self.screed_c
        if self.eco_setpoint_c is None:
            self.eco_setpoint_c = self.setpoint_c - self.swing_k
        if self.window_m2 is None:
            self.window_m2 = self.area_m2 * WINDOW_FRACTION

    # -- derived capacities ------------------------------------------------- #

    @property
    def nameplate_w(self) -> float:
        """Cable nameplate, W."""
        return self.area_m2 * self.w_per_m2

    @property
    def kwh_per_k(self) -> float:
        """Screed heat capacity, kWh/K - 0.0275 kWh/K per m² at 50 mm."""
        mass_kg = self.area_m2 * (self.screed_mm / 1000.0) * SCREED_RHO
        return mass_kg * SCREED_CP_J_PER_KG_K / J_PER_KWH

    @property
    def room_kwh_per_k(self) -> float:
        """Room heat capacity, kWh/K (effective building mass, D4 §5.7)."""
        return self.area_m2 * ROOM_HEIGHT_M * ROOM_MASS_KWH_PER_K_PER_M3

    @property
    def stored_kwh(self) -> float:
        """Energy in both nodes above 0 °C - the balance test's state term."""
        return self.kwh_per_k * self.screed_c + self.room_kwh_per_k * self.room_c

    # -- physics ------------------------------------------------------------ #

    def _target_c(self) -> float | None:
        if self.mode == "off":
            return None
        if self.mode == "eco":
            assert self.eco_setpoint_c is not None
            return self.eco_setpoint_c
        return self.setpoint_c

    def _thermostat(self) -> None:
        """Hysteresis on the floor sensor, with the hardware limits on top."""
        target = self._target_c()
        if self._sensor_c < self.floor_min_c:
            self.relay_on = True  # provisioned hardware backstop (INV-64)
            return
        if target is None or self._sensor_c >= self.max_c:
            self.relay_on = False
            return
        if self.relay_on:
            self.relay_on = self._sensor_c < target
        else:
            self.relay_on = self._sensor_c < target - self.swing_k

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Advance both nodes `dt_s` seconds and report the two sensors."""
        if command is not None and (command.setpoint_c is not None or command.mode is not None):
            self._pending = command
            self._pending_in_s = COMMAND_LATENCY_S
        if self._pending is not None:
            self._pending_in_s -= dt_s
            if self._pending_in_s <= 0.0:
                if self._pending.setpoint_c is not None:
                    self.setpoint_c = self._pending.setpoint_c
                if self._pending.mode is not None:
                    self.mode = self._pending.mode
                self._pending = None

        self._thermostat()
        p_heat = self.nameplate_w if self.relay_on else 0.0
        assert self.window_m2 is not None
        q_solar = self.window_m2 * env.solar_w_per_m2 * WINDOW_SHGC

        ua_up = H_FLOOR_UP_W_PER_M2K * self.area_m2
        ua_ground = U_GROUND_W_PER_M2K * self.area_m2
        ua_envelope = self.u_envelope_w_per_m2k * self.area_m2

        q_up = ua_up * (self.screed_c - self.room_c)
        q_ground = ua_ground * (self.screed_c - env.ground_c)
        q_envelope = ua_envelope * (self.room_c - env.outdoor_c)

        self.screed_c += (p_heat - q_up - q_ground) * dt_s / (self.kwh_per_k * J_PER_KWH)
        self.room_c += (q_up + q_solar - q_envelope) * dt_s / (self.room_kwh_per_k * J_PER_KWH)

        self.energy_in_kwh += kwh(p_heat, dt_s)
        self.solar_in_kwh += kwh(q_solar, dt_s)
        self.loss_kwh += kwh(q_ground + q_envelope, dt_s)

        # The floor sensor is cast into the screed: it lags and it quantises.
        alpha = min(1.0, dt_s / SENSOR_TAU_S)
        self._sensor_c += (self.screed_c - self._sensor_c) * alpha

        return Reads(
            power_w=p_heat,
            amps=amps_1p(p_heat),
            status="heat" if self.relay_on else self.mode,
            values={
                TEMP_FLOOR: _quantise(self._sensor_c),
                TEMP_AIR: _quantise(self.room_c),
                SETPOINT_C: self.setpoint_c,
            },
        )


def _quantise(value: float) -> float:
    """Round to the 0.1 K the device reports."""
    return round(value / SENSOR_STEP_K) * SENSOR_STEP_K
