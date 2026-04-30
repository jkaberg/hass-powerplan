"""A plain switched appliance: a plug, a nameplate, nothing else to model (D9 §5.9).

The sauna of the reference house - "6 kW, Saturdays 19:00 for 90 min,
`generic_switch` with force" - and any appliance whose only handle is on/off.
The household turns it on; the controller may only shed it, and under `force`
not even that. No thermal model of the room: the heater's own thermostat
cycles inside the box once the stove is hot, which the meter sees as a lower
steady draw after the heat-up.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import Command, Env, Reads, amps_3p, kwh

SAUNA_W = 6000.0
HEAT_UP_S = 1800.0
HOLD_DUTY = 0.8
SESSION_S = 5400.0

SOURCES: dict[str, str] = {
    "SAUNA_W": "D9 §5.9 house spec: sauna 6 kW (`assumed`)",
    "SESSION_S": "D9 §5.9 house spec: Saturdays 19:00 for 90 min",
    "HEAT_UP_S": (
        "assumed: a 6 kW stove brings a family sauna to temperature in about half an hour, "
        "full power throughout"
    ),
    "HOLD_DUTY": (
        "assumed: the stove's thermostat then cycles at roughly 80 % duty to hold 80 °C; "
        "replaced by a measured session from the reference house"
    ),
}


@dataclass(slots=True)
class SwitchSim:
    """An on/off appliance with a heat-up and a hold phase."""

    watts: float = SAUNA_W
    plug_on: bool = False
    on_s: float = 0.0
    energy_in_kwh: float = 0.0

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Follow the plug; draw full power while heating up, the hold duty after."""
        del env
        if command is not None and command.on is not None:
            was_on = self.plug_on
            self.plug_on = command.on
            if self.plug_on and not was_on:
                self.on_s = 0.0
        power_w = 0.0
        if self.plug_on:
            self.on_s += dt_s
            power_w = self.watts if self.on_s <= HEAT_UP_S else self.watts * HOLD_DUTY
        self.energy_in_kwh += kwh(power_w, dt_s)
        return Reads(
            power_w=power_w,
            amps=amps_3p(power_w),
            status="on" if self.plug_on else "off",
            values={},
        )
