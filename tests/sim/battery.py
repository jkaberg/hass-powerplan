"""A home battery behind an inverter: a signed setpoint and a state of charge (D4 §6.6).

The inverter takes a power setpoint in watts - positive charges, negative
discharges - and follows it within its own limits and the cells' window: it
never charges above full or discharges below empty, whatever it is told. The
state of charge moves by what went in times the charge efficiency and by what
came out divided by the discharge efficiency, so a round trip loses what the
datasheet says it does. Released (setpoint 0) it neither charges nor discharges,
which is the fail-safe the type writes (INV-64).

Power is signed the way the meter signs it (import +, export −), so the runner
adds `power_w` into the house's total like any other load.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import SOC, Command, Env, Reads, amps_3p, kwh

#: A 10 kWh LFP battery on a 5 kW hybrid inverter - the size D4 §6.6's example
#: and `tests/core/strategies/conftest.py::battery_store` use.
CAPACITY_KWH = 10.0
MAX_CHARGE_W = 5000.0
MAX_DISCHARGE_W = 5000.0
#: LFP: 95 % one way, the usual inverter datasheet figure (D4 §6.6 `CHEMISTRIES`).
CHARGE_EFF = 0.95
DISCHARGE_EFF = 0.95
#: Full, in percent of the usable capacity.
FULL_PCT = 100.0

SOURCES: dict[str, str] = {
    "CAPACITY_KWH": (
        "assumed: a common 10 kWh home battery (D4 §6.6's example); replaced by a real "
        "installation's datasheet"
    ),
    "MAX_CHARGE_W": "assumed: a 5 kW hybrid inverter, the size sold beside 10 kWh",
    "MAX_DISCHARGE_W": "assumed: the same 5 kW inverter, symmetric",
    "CHARGE_EFF": "D4 §6.6 CHEMISTRIES['lfp'].charge_eff (inverter datasheet round trip ≈ 90 %)",
    "DISCHARGE_EFF": "D4 §6.6 CHEMISTRIES['lfp'].discharge_eff",
    "FULL_PCT": "percent",
}


@dataclass(slots=True)
class BatterySim:
    """A battery that follows its inverter setpoint inside the cells' window."""

    soc_pct: float = 50.0
    capacity_kwh: float = CAPACITY_KWH
    max_charge_w: float = MAX_CHARGE_W
    max_discharge_w: float = MAX_DISCHARGE_W
    charge_eff: float = CHARGE_EFF
    discharge_eff: float = DISCHARGE_EFF
    setpoint_w: float = 0.0

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Follow the setpoint, clamped to the inverter and to empty and full."""
        del env
        if command is not None and command.power_w is not None:
            self.setpoint_w = command.power_w
        per_pct_kwh = self.capacity_kwh / FULL_PCT
        hours = dt_s / 3600.0
        power_w = 0.0
        if self.setpoint_w > 0.0 and hours > 0.0:
            room_kwh = (FULL_PCT - self.soc_pct) * per_pct_kwh / self.charge_eff
            power_w = min(self.setpoint_w, self.max_charge_w, room_kwh / hours * 1000.0)
            self.soc_pct += kwh(power_w, dt_s) * self.charge_eff / per_pct_kwh
        elif self.setpoint_w < 0.0 and hours > 0.0:
            held_kwh = self.soc_pct * per_pct_kwh * self.discharge_eff
            out_w = min(-self.setpoint_w, self.max_discharge_w, held_kwh / hours * 1000.0)
            self.soc_pct -= kwh(out_w, dt_s) / self.discharge_eff / per_pct_kwh
            power_w = -out_w
        self.soc_pct = min(FULL_PCT, max(0.0, self.soc_pct))
        return Reads(
            power_w=power_w,
            amps=amps_3p(abs(power_w)),
            status="charging" if power_w > 0.0 else ("discharging" if power_w < 0.0 else "idle"),
            values={SOC: self.soc_pct},
        )
