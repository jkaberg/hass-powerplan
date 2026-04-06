"""A bedroom with an 800 W panel heater on a smart plug (D9 §5.9).

One node - the room's effective building mass - a panel heater with its own
mechanical thermostat, and a plug in front of it.  The quirk that matters is
what the plug *cannot* do: it switches, it does not set a temperature.  Turning
the plug on does not make the heater heat if its dial is satisfied, and the
controller therefore never gets a guaranteed 800 W out of a SWITCH grant
(D4 §5.6, §6.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import SETPOINT_C, TEMP_AIR, Command, Env, Reads, amps_1p, kwh

PANEL_W = 800.0
DIAL_SWING_K = 0.5
DEFAULT_U_ENVELOPE_W_PER_M2K = 0.7
ROOM_HEIGHT_M = 2.5
ROOM_MASS_KWH_PER_K_PER_M3 = 0.03
WINDOW_FRACTION = 0.15
WINDOW_SHGC = 0.5
SENSOR_STEP_K = 0.1
PLUG_LATENCY_S = 1.0
J_PER_KWH = 3_600_000.0

SOURCES: dict[str, str] = {
    "PANEL_W": "D4 §6.5 radiator nameplate defaults: panel heater 800 W",
    "DIAL_SWING_K": (
        "assumed: 0.5 K differential on a mechanical bimetal room thermostat. Replaced by "
        "the measured on/off band of the reference house's bedroom heaters"
    ),
    "DEFAULT_U_ENVELOPE_W_PER_M2K": "D4 §6.4 building-age table, 2000–2010 → 0.7 W/m²·K",
    "ROOM_HEIGHT_M": "D4 §6.4 ('heated area × 2.5 m' gives RoomStore volume)",
    "ROOM_MASS_KWH_PER_K_PER_M3": "D4 §5.7 RoomStore default (0.03 kWh/K per m³, timber)",
    "WINDOW_FRACTION": "assumed: window area 15 % of floor area (TEK17 §13-7 caps it at 25 %)",
    "WINDOW_SHGC": "assumed: g ≈ 0.5 for Nordic triple glazing",
    "SENSOR_STEP_K": "a room temperature sensor in HA reports 0.1 K steps",
    "PLUG_LATENCY_S": (
        "assumed: 1 s for a Zigbee/Wi-Fi plug to switch and report. Replaced by a captured "
        "plug's state-change timing"
    ),
    "J_PER_KWH": "SI",
}


@dataclass(slots=True)
class RoomSim:
    """A single-node room heated by a plug-controlled panel heater."""

    area_m2: float
    nameplate_w: float = PANEL_W
    u_envelope_w_per_m2k: float = DEFAULT_U_ENVELOPE_W_PER_M2K
    window_m2: float | None = None
    dial_c: float = 19.0
    dial_swing_k: float = DIAL_SWING_K
    dial_writable: bool = False
    room_c: float = 19.0
    plug_on: bool = True
    heating: bool = False
    energy_in_kwh: float = 0.0
    solar_in_kwh: float = 0.0
    loss_kwh: float = 0.0
    _pending_on: bool | None = field(default=None)
    _pending_in_s: float = field(default=0.0)

    def __post_init__(self) -> None:
        """Default the glazing from the floor area."""
        if self.window_m2 is None:
            self.window_m2 = self.area_m2 * WINDOW_FRACTION

    @property
    def kwh_per_k(self) -> float:
        """Room heat capacity, kWh/K."""
        return self.area_m2 * ROOM_HEIGHT_M * ROOM_MASS_KWH_PER_K_PER_M3

    @property
    def stored_kwh(self) -> float:
        """Energy in the room node above 0 °C."""
        return self.kwh_per_k * self.room_c

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Advance the room `dt_s` seconds; the plug gates the dial, not vice versa."""
        if command is not None:
            if command.on is not None:
                self._pending_on = command.on
                self._pending_in_s = PLUG_LATENCY_S
            if command.setpoint_c is not None and self.dial_writable:
                self.dial_c = command.setpoint_c
        if self._pending_on is not None:
            self._pending_in_s -= dt_s
            if self._pending_in_s <= 0.0:
                self.plug_on = self._pending_on
                self._pending_on = None

        # The heater's own thermostat, then the plug in front of it.
        if self.heating:
            self.heating = self.room_c < self.dial_c
        else:
            self.heating = self.room_c < self.dial_c - self.dial_swing_k
        power_w = self.nameplate_w if (self.plug_on and self.heating) else 0.0

        assert self.window_m2 is not None
        q_solar = self.window_m2 * env.solar_w_per_m2 * WINDOW_SHGC
        q_loss = self.u_envelope_w_per_m2k * self.area_m2 * (self.room_c - env.outdoor_c)
        self.room_c += (power_w + q_solar - q_loss) * dt_s / (self.kwh_per_k * J_PER_KWH)

        self.energy_in_kwh += kwh(power_w, dt_s)
        self.solar_in_kwh += kwh(q_solar, dt_s)
        self.loss_kwh += kwh(q_loss, dt_s)

        return Reads(
            power_w=power_w,
            amps=amps_1p(power_w),
            status="on" if self.plug_on else "off",
            values={
                TEMP_AIR: round(self.room_c / SENSOR_STEP_K) * SENSOR_STEP_K,
                SETPOINT_C: self.dial_c,
            },
        )
