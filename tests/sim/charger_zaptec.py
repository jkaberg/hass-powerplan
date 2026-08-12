"""A Zaptec charger behind its installation's *Available current* - where a change costs.

Zaptec steers a charger through the installation: one cloud number, *Available
current*, that the charger then offers the car (custom-components/zaptec v0.8.7,
`number.py`). The cloud takes every write - it is an HTTP API that either
returned or raised - but the car does not like being told often: "To keep
charging stable, update AvailableCurrent no more than once every 15 minutes.
Frequent current or phase changes may cause the vehicle to interrupt the charging
session" (docs.zaptec.com, dynamic load balancing with the Zaptec API). This
wrapper is that quirk, and only that: the battery, the taper and the 6 A cliff
stay in `ev.py`.

* **A change inside 15 minutes of the last one** is counted, and interrupts the
  session with a seeded probability: the car stops drawing and ignores the pilot
  for the same ten minutes a dropped session costs at the cliff.
* **The limit is the switch.** 0 A pauses the car and the session survives it;
  6 A or more resumes it. There is no enable to write - the integration's
  *Charging* switch is not powerplan's (D-0372).
* **The status speaks Zaptec**: `disconnected`, `connected_requesting`,
  `connected_charging`, `connected_finished` (the integration's lower-cased
  `ChargerOperationModes`, README "Changes from 0.7.x to 0.8.x").
* **The read-back is prompt**: after a write the integration re-polls the
  installation 2 s and 7 s later (`const.py`, `ZAPTEC_POLL_INSTALLATION_TRIGGER_DELAYS`),
  well inside one 10 s tick, so the limit a step reports is the one it holds.

`raises_too_soon` is the count the scenario reads: a *raise* inside the window is
a non-urgent change (a reduction is the gate's urgent path, D4 §5.10), and the
one the charger's own guidance is about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .base import LIMIT_A, Command, Env, Reads, derive_rng
from .ev import AMP_EPS, AWAITING_START, CHARGING, COMPLETED, DISCONNECTED, MIN_A, REARM_S, EvSim

CHANGE_WINDOW_S = 900.0
INTERRUPT_P = 0.25

#: The Easee-worded statuses `EvSim` reports, in Zaptec's own words.
ZAPTEC_STATUS: dict[str, str] = {
    DISCONNECTED: "disconnected",
    AWAITING_START: "connected_requesting",
    CHARGING: "connected_charging",
    COMPLETED: "connected_finished",
}

SOURCES: dict[str, str] = {
    "CHANGE_WINDOW_S": (
        "docs.zaptec.com, 'Dynamic load balancing with the Zaptec API': update AvailableCurrent "
        "no more than once every 15 minutes; custom-components/zaptec README v0.8.7, 'Setting "
        "charging current': Zaptec recommends not changing the values more often than every "
        "15 minutes"
    ),
    "INTERRUPT_P": (
        "assumed: a quarter of the changes inside the window interrupt the session. Zaptec says "
        "only that frequent changes 'may cause the vehicle to interrupt the charging session'; "
        "replaced by a measured rate when a Zaptec house reports one"
    ),
    "REARM_S": "see sim/ev.py — the car ignores the charger for about ten minutes after a drop",
    "MIN_A": "see sim/ev.py — IEC 61851's 6 A floor: 6 A or more offered again resumes the car",
    "AMP_EPS": "see sim/ev.py",
    "ZAPTEC_STATUS": (
        "custom-components/zaptec v0.8.7 sensor.py (ZaptecChargeSensor's icon map) and README "
        "'Changes from 0.7.x to 0.8.x': the five lower-cased operation modes. assumed: a car "
        "held at 0 A reports connected_requesting ('Waiting'), not connected_finished"
    ),
}


@dataclass(slots=True)
class ZaptecChargerSim:
    """An `EvSim` behind a Zaptec installation's Available current."""

    ev: EvSim
    seed: int = 0
    #: The installation's *Available current*, as the cloud holds it and the
    #: integration reads it back. Not the car's pilot limit: 0 A pauses the car
    #: and leaves `EvSim.limit_a` where it was.
    available_a: float = 32.0
    changes: int = 0
    changes_too_soon: int = 0
    raises_too_soon: int = 0
    interruptions: int = 0
    _last_change_at: datetime | None = field(default=None)
    #: Every change the charger took: `(instant, amps before, amps after)`.
    log: list[tuple[datetime, float, float]] = field(default_factory=list)

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Apply the installation's new Available current, if any, then step the car."""
        forwarded = None if command is None else self._apply(command, env.now)
        reads = self.ev.step(dt_s, forwarded, env)
        return Reads(
            power_w=reads.power_w,
            amps=reads.amps,
            available=True,
            status=ZAPTEC_STATUS[reads.status or self.ev.status],
            values={**reads.values, LIMIT_A: self.available_a},
        )

    def _apply(self, command: Command, now: datetime) -> Command | None:
        """Turn one write of Available current into what the car sees."""
        if command.limit_a is None:
            return None
        before = self.available_a
        wanted = command.limit_a
        if abs(wanted - before) <= AMP_EPS:
            return None
        self.available_a = wanted
        self.changes += 1
        self.log.append((now, before, wanted))
        soon = self._last_change_at is not None and (now - self._last_change_at) < timedelta(
            seconds=CHANGE_WINDOW_S
        )
        self._last_change_at = now
        if soon:
            self.changes_too_soon += 1
            if wanted > before:
                self.raises_too_soon += 1
            self._maybe_interrupt()
        if wanted >= MIN_A - AMP_EPS and self.ev.paused:
            # Current back on the installation: the charger offers it again.
            return Command(on=True, limit_a=wanted)
        return Command(limit_a=wanted)

    def _maybe_interrupt(self) -> None:
        """Let the car take a too-frequent change as the end of its session, sometimes."""
        ev = self.ev
        if not ev.plugged or ev.paused or ev.actual_a <= AMP_EPS:
            return
        rng = derive_rng(self.seed, "zaptec_change", self.changes)
        if rng.random() < INTERRUPT_P:
            self.interruptions += 1
            ev.sessions_dropped += 1
            ev.rearm_in_s = max(ev.rearm_in_s, REARM_S)
