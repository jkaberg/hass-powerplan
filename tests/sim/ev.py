"""An EV on a three-phase 32 A charger in a 230 V IT net - with the 6 A cliff.

The physics is a 60 kWh battery that tapers near full. The behaviour that matters is the
cliff: IEC 61851 signals available current to the car as a PWM duty cycle and the
mapping **bottoms out at 6 A**. There is no duty cycle that means 4 A. Write below 6 and
the charger stops offering a valid pilot, the car opens its contactor and the session
ends - and a car whose session has ended does not look at the charger again for about
ten minutes. One night on the ancestor controller that cost twelve dropped sessions and
0.2–3.4 kWh delivered in hours where 6.5 kWh was available (the ancestor controller's
README, "6 A is a cliff, not a slope").

A grant of exactly zero is different: it is a deliberate pause through the
charger's own switch, the session survives it, and resuming takes a handshake,
not a re-arm (INV-25 - a zero grant is not a shed).

Transport quirks (link drops, lost writes, read-back latency) are not here:
they belong to `charger_ble.py`, which wraps this.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import (
    LIMIT_A,
    SOC,
    W_PER_AMP_IT230_3P,
    Command,
    Env,
    Reads,
    kwh,
)

CAPACITY_KWH = 60.0
MAX_A = 32.0
MIN_A = 6.0
CHARGE_EFFICIENCY = 0.90
TAPER_FROM_SOC = 0.80
TAPER_FLOOR_FRACTION = 0.25
REARM_S = 602.0
RESUME_S = 30.0
RAMP_A_PER_S = 1.6
AMP_EPS = 1e-6
LIMIT_SOC = 1.0

SOURCES: dict[str, str] = {
    "CAPACITY_KWH": "D9 §5.9 house spec: 60 kWh battery",
    "MAX_A": "D9 §5.9 house spec: 3φ 32 A charger; D4 §6.2 charger-maximum options",
    "MIN_A": "IEC 61851 control-pilot PWM bottoms out at 6 A; D4 §6.2 advanced 'min A 6'",
    "CHARGE_EFFICIENCY": "D4 §6.2 advanced: efficiency 0.90",
    "TAPER_FROM_SOC": (
        "D4 §6.2 'charge to 80 %' is where battery-health advice stops; AC charging above it "
        "is BMS-limited. assumed: the taper starts at 80 %. Replaced by a captured AC charge "
        "curve from the reference house's car"
    ),
    "TAPER_FLOOR_FRACTION": (
        "assumed: the car accepts 25 % of the offered power at 100 % SoC, linear from 80 %. "
        "Replaced by a captured charge curve"
    ),
    "REARM_S": (
        "effektstyring README: after a dropped session the car ignores the charger for a "
        "measured 10 m 02 s – 10 m 09 s; 602 s is the low end"
    ),
    "RESUME_S": (
        "assumed: 30 s for the contactor handshake after a deliberate pause is released. "
        "Replaced by the measured resume time of the Easee charger"
    ),
    "RAMP_A_PER_S": (
        "assumed: the car follows a new pilot limit at ~1.6 A/s (32 A in 20 s). Replaced by "
        "a captured step response; D4 §6.2's 'settle 60 s' is the controller's wait, not this"
    ),
    "AMP_EPS": "effektstyring README: AMP_EPS = 1e-6 A absorbs the float round trip",
    "LIMIT_SOC": (
        "the car's own charge limit, set in its app; 1.0 unless the house says otherwise. "
        "The reference house sets the 80 % it also gave powerplan (D4 §6.2 'charge to 80 %'), "
        "so a car nobody steers stops where the shadow does (D11 §5.3, `nordic_detached@2`)"
    ),
    "W_PER_AMP_IT230_3P": "see sim/base.py – D3 §5.1, √3 × 230 for a 3φ load in a 230 V IT net",
}

#: Charger statuses, in the vocabulary the Easee BLE integration reports.
CHARGING = "charging"
AWAITING_START = "awaiting_start"
COMPLETED = "completed"
DISCONNECTED = "disconnected"


@dataclass(slots=True)
class EvSim:
    """A car and its charger: battery, taper, the 6 A cliff, pause and resume."""

    capacity_kwh: float = CAPACITY_KWH
    max_a: float = MAX_A
    w_per_amp: float = W_PER_AMP_IT230_3P
    soc: float = 0.4
    #: The car's own charge limit (`SOURCES["LIMIT_SOC"]`): it stops itself here.
    limit_soc: float = LIMIT_SOC
    plugged: bool = True
    limit_a: float = MAX_A
    actual_a: float = 0.0
    paused: bool = False
    rearm_in_s: float = 0.0
    resume_in_s: float = 0.0
    session_kwh: float = 0.0
    sessions_dropped: int = 0
    energy_in_kwh: float = 0.0

    # -- the household's side ----------------------------------------------- #

    def plug_in(self) -> None:
        """Plug the car in on arrival, starting a fresh session."""
        self.plugged = True
        self.rearm_in_s = 0.0
        self.resume_in_s = RESUME_S
        self.session_kwh = 0.0

    def unplug(self, drive_kwh: float) -> None:
        """Unplug the car and spend `drive_kwh` on the trip."""
        self.plugged = False
        self.actual_a = 0.0
        self.soc = max(0.0, self.soc - drive_kwh / self.capacity_kwh)

    # -- physics ------------------------------------------------------------ #

    def taper_fraction(self) -> float:
        """Fraction of the offered power the battery accepts at this SoC."""
        if self.soc <= TAPER_FROM_SOC:
            return 1.0
        span = 1.0 - TAPER_FROM_SOC
        over = min(1.0, (self.soc - TAPER_FROM_SOC) / span)
        return 1.0 - over * (1.0 - TAPER_FLOOR_FRACTION)

    def _apply(self, command: Command) -> None:
        if command.on is not None:
            if command.on and self.paused:
                self.paused = False
                self.resume_in_s = RESUME_S
            elif not command.on:
                self.paused = True
        if command.limit_a is None:
            return
        wanted = command.limit_a
        if wanted <= AMP_EPS:
            # A zero grant is a deliberate stop. The session survives it.
            self.paused = True
            return
        if wanted < MIN_A - AMP_EPS:
            # The cliff. No valid pilot, the car ends the session, ten minutes gone.
            if self.plugged and not self.paused and self.rearm_in_s <= 0.0:
                self.sessions_dropped += 1
            self.rearm_in_s = REARM_S
            self.actual_a = 0.0
            self.limit_a = wanted
            return
        self.limit_a = min(wanted, self.max_a)

    @property
    def status(self) -> str:
        """What the charger's status sensor would say."""
        if not self.plugged:
            return DISCONNECTED
        if self.soc >= self.limit_soc:
            return COMPLETED
        if self.actual_a > AMP_EPS:
            return CHARGING
        return AWAITING_START

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Follow the pilot limit, charge the battery and report."""
        del env  # an EV's physics does not depend on the weather in this model
        if command is not None:
            self._apply(command)

        self.rearm_in_s = max(0.0, self.rearm_in_s - dt_s)
        self.resume_in_s = max(0.0, self.resume_in_s - dt_s)

        blocked = (
            not self.plugged
            or self.paused
            or self.rearm_in_s > 0.0
            or self.resume_in_s > 0.0
            or self.soc >= self.limit_soc
            or self.limit_a < MIN_A - AMP_EPS
        )
        target_a = 0.0 if blocked else min(self.limit_a, self.max_a)
        ramp = RAMP_A_PER_S * dt_s
        if target_a > self.actual_a:
            self.actual_a = min(target_a, self.actual_a + ramp)
        else:
            self.actual_a = max(target_a, self.actual_a - ramp)

        # `actual_a` follows the pilot; the battery's taper is what the meter sees.
        offered_w = self.actual_a * self.w_per_amp
        drawn_w = offered_w * self.taper_fraction()
        drawn_a = drawn_w / self.w_per_amp
        delivered = kwh(drawn_w, dt_s) * CHARGE_EFFICIENCY
        self.soc = min(1.0, self.soc + delivered / self.capacity_kwh)
        self.session_kwh += kwh(drawn_w, dt_s)
        self.energy_in_kwh += kwh(drawn_w, dt_s)

        return Reads(
            power_w=drawn_w,
            amps=(drawn_a, drawn_a, drawn_a),
            status=self.status,
            values={SOC: self.soc * 100.0, LIMIT_A: self.limit_a},
        )
