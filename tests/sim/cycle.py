"""An appliance cycle: a dishwasher eco programme that cannot be paused.

D4 §6.8 gives the product one number per programme - 0.9 kWh over 3 h for a
dishwasher eco cycle.  A real cycle is not 300 W for three hours: it is two
1.8 kW heating spikes inside three hours of 70 W circulation, and the spikes are
where a capacity window is won or lost.  The profile below spends 0.895 kWh over
exactly 180 minutes in that shape.

The quirk is the one D4 §5.13 names: a cycle in progress is not interruptible.
Cutting power mid-programme does not pause it - the appliance aborts, and the
next start runs the whole programme again from the beginning, paying the energy
twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .base import CYCLE_MINUTE, ENERGY_KWH, Command, Env, Reads, amps_1p, kwh

CIRCULATION_W = 70.0
HEAT_W = 1800.0
DRAIN_W = 60.0
DRY_W = 5.0
PREWASH_MIN = 20.0
MAIN_HEAT_MIN = 16.0
MAIN_WASH_MIN = 45.0
DRAIN_MIN = 5.0
RINSE_HEAT_MIN = 10.0
RINSE_MIN = 25.0
DRY_MIN = 59.0

SOURCES: dict[str, str] = {
    "CIRCULATION_W": (
        "assumed: circulation pump plus electronics, fitted so the segments below total "
        "0.895 kWh — D4 §6.8's 0.9 kWh eco figure (EU energy-label typicals)"
    ),
    "HEAT_W": (
        "assumed: a domestic dishwasher's heating element is 1.8–2.2 kW; 1.8 kW is the low end. "
        "Replaced by a captured power trace of the reference house's machine"
    ),
    "DRAIN_W": "assumed: drain and fill pumps",
    "DRY_W": "assumed: residual-heat drying draws only electronics",
    "PREWASH_MIN": "assumed: segment durations sum to D4 §6.8's 3 h 00 eco duration",
    "MAIN_HEAT_MIN": "assumed: as above; the two heat segments carry 0.78 of the 0.895 kWh",
    "MAIN_WASH_MIN": "assumed: as above",
    "DRAIN_MIN": "assumed: as above",
    "RINSE_HEAT_MIN": "assumed: as above",
    "RINSE_MIN": "assumed: as above",
    "DRY_MIN": "assumed: as above — the seven segments total exactly 180 min",
}

#: (label, watts, minutes) - the eco programme, in order.
DISHWASHER_ECO: tuple[tuple[str, float, float], ...] = (
    ("prewash", CIRCULATION_W, PREWASH_MIN),
    ("main_heat", HEAT_W, MAIN_HEAT_MIN),
    ("main_wash", CIRCULATION_W, MAIN_WASH_MIN),
    ("drain_fill", DRAIN_W, DRAIN_MIN),
    ("rinse_heat", HEAT_W, RINSE_HEAT_MIN),
    ("rinse", CIRCULATION_W, RINSE_MIN),
    ("dry", DRY_W, DRY_MIN),
)

POWERED = "powered"
IDLE = "idle"
RUNNING = "running"
FINISHED = "finished"
ABORTED = "aborted"


@dataclass(slots=True)
class CycleSim:
    """A non-interruptible appliance programme."""

    profile: Sequence[tuple[str, float, float]] = DISHWASHER_ECO
    state: str = IDLE
    powered: bool = True
    requested: bool = False
    elapsed_s: float = 0.0
    energy_in_kwh: float = 0.0
    runs_completed: int = 0
    restarts: int = 0
    _segment: str = field(default="")

    @property
    def duration_s(self) -> float:
        """Total programme length, seconds."""
        return sum(minutes for _, _, minutes in self.profile) * 60.0

    @property
    def programme_kwh(self) -> float:
        """Energy the whole programme spends, kWh."""
        return sum(watts * minutes / 60.0 for _, watts, minutes in self.profile) / 1000.0

    def request(self) -> None:
        """Load the machine: the household asks for this cycle to be ready."""
        self.requested = True

    def _power_now(self) -> float:
        cursor = 0.0
        for label, watts, minutes in self.profile:
            cursor += minutes * 60.0
            if self.elapsed_s < cursor:
                self._segment = label
                return watts
        self._segment = ""
        return 0.0

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Run the programme, or abort it if power is taken away mid-cycle."""
        del env
        if command is not None:
            was_powered = self.powered
            if command.on is not None:
                self.powered = command.on
            starts = bool(command.start)
            # The smart-plug pattern: a loaded machine whose start was pressed
            # with the plug off begins the moment power comes back. Quirk, not
            # convenience - it is how a plug-controlled dishwasher is run.
            if self.powered and not was_powered and self.requested:
                starts = True
            if starts and self.state in (IDLE, FINISHED, ABORTED) and self.powered:
                if self.state == ABORTED:
                    self.restarts += 1
                self.state = RUNNING
                self.elapsed_s = 0.0

        power_w = 0.0
        if self.state == RUNNING:
            if not self.powered:
                # Not a pause. The programme is lost and must run again from zero.
                self.state = ABORTED
                self.elapsed_s = 0.0
            else:
                power_w = self._power_now()
                self.elapsed_s += dt_s
                if self.elapsed_s >= self.duration_s:
                    self.state = FINISHED
                    self.requested = False
                    self.runs_completed += 1

        self.energy_in_kwh += kwh(power_w, dt_s)
        return Reads(
            power_w=power_w,
            amps=amps_1p(power_w),
            status=self._segment if self.state == RUNNING else self.state,
            values={
                CYCLE_MINUTE: self.elapsed_s / 60.0,
                ENERGY_KWH: self.energy_in_kwh,
                POWERED: 1.0 if self.powered else 0.0,
            },
        )
