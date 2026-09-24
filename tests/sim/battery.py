"""A home battery behind an inverter: a setpoint or a command, and a state of charge (D4 §6.6).

Two ways to be told what to do, as the two kinds tell a battery (D4 §4.2):

* a signed power setpoint in watts (`MODULATE`, a bare number) - positive
  charges, negative discharges; released at 0 it neither charges nor discharges,
  the fail-safe that kind writes (INV-64);
* a command (`battery`): `self_use` balances the house the way a hybrid
  inverter does by itself - it charges from what the house would export and
  discharges into what it would import; `hold` only charges from the export;
  `charge:W` and `discharge:W` follow W; a bare `charge` or `discharge` runs at
  the inverter's own rate.

`self_use` needs the house's net power without the battery, `house_w`, which
the runner sets before it steps a commanded battery (it steps it after every
other load). `hold_as_self_use` is the inverter before WP7.9's rows: it had no
hold, and a planned hold went out as its own self-use - the regression
`battery_holds_for_peak` guards (D9 §5.3).

The state of charge moves by what went in times the charge efficiency and by
what came out divided by the discharge efficiency, so a round trip loses what
the datasheet says it does. Power is signed the way the meter signs it
(import +, export −), so the runner adds `power_w` into the house's total like
any other load.
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
    """A battery that follows its inverter's setpoint or command inside the cells' window."""

    soc_pct: float = 50.0
    capacity_kwh: float = CAPACITY_KWH
    max_charge_w: float = MAX_CHARGE_W
    max_discharge_w: float = MAX_DISCHARGE_W
    charge_eff: float = CHARGE_EFF
    discharge_eff: float = DISCHARGE_EFF
    setpoint_w: float = 0.0
    #: The command it follows, `None` for a bare setpoint.
    command: str | None = None
    #: The house's net power without the battery, W - set by the runner each step.
    house_w: float = 0.0
    #: Before WP7.9's rows: a hold went out as the inverter's own self-use.
    hold_as_self_use: bool = False
    #: The inverter's own minimum for its self-use, % - set to the household's
    #: reserve, as an installer sets it (D4 §6.6's 20 %).
    self_use_floor_pct: float = 20.0

    @property
    def self_use(self) -> bool:
        """Whether this battery is told commands, and so balances the house by itself."""
        return self.command is not None

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Follow the setpoint or the command, clamped to the inverter and to empty and full."""
        del env
        if command is not None and command.power_w is not None:
            self.setpoint_w = command.power_w
        if command is not None and command.battery is not None:
            self.command = command.battery
        wanted = self._wanted() if self.command is not None else self.setpoint_w
        power_w = self._move(wanted, dt_s)
        return Reads(
            power_w=power_w,
            amps=amps_3p(abs(power_w)),
            status="charging" if power_w > 0.0 else ("discharging" if power_w < 0.0 else "idle"),
            values={SOC: self.soc_pct},
        )

    def _wanted(self) -> float:
        """Return the signed power the command asks for this step."""
        word, _, number = (self.command or "self_use").partition(":")
        if word == "hold" and self.hold_as_self_use:
            word = "self_use"
        if word == "self_use":
            if self.house_w > 0.0 and self.soc_pct <= self.self_use_floor_pct:
                return 0.0
            return -self.house_w
        if word == "hold":
            return max(0.0, -self.house_w)
        if word == "charge":
            return float(number) if number else self.max_charge_w
        if word == "discharge":
            return -(float(number) if number else self.max_discharge_w)
        return 0.0

    def _move(self, wanted_w: float, dt_s: float) -> float:
        """Charge or discharge `wanted_w` within the inverter and the cells; return the power."""
        per_pct_kwh = self.capacity_kwh / FULL_PCT
        hours = dt_s / 3600.0
        power_w = 0.0
        if wanted_w > 0.0 and hours > 0.0:
            room_kwh = (FULL_PCT - self.soc_pct) * per_pct_kwh / self.charge_eff
            power_w = min(wanted_w, self.max_charge_w, room_kwh / hours * 1000.0)
            self.soc_pct += kwh(power_w, dt_s) * self.charge_eff / per_pct_kwh
        elif wanted_w < 0.0 and hours > 0.0:
            held_kwh = self.soc_pct * per_pct_kwh * self.discharge_eff
            out_w = min(-wanted_w, self.max_discharge_w, held_kwh / hours * 1000.0)
            self.soc_pct -= kwh(out_w, dt_s) / self.discharge_eff / per_pct_kwh
            power_w = -out_w
        self.soc_pct = min(FULL_PCT, max(0.0, self.soc_pct))
        return power_w
