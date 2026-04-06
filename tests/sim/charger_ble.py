"""The Bluetooth transport in front of the charger - where writes go to die.

A cloud HTTP call either returned or raised. A Bluetooth write can be accepted by Home
Assistant and never reach the charger, and the link can vanish with the entities left
`unavailable` (the ancestor controller's README, "A different transport fails
differently"). This wrapper is that transport, and only that: the battery, the taper and
the 6 A cliff stay in `ev.py`.

Four quirks, all of them things the reference house did:

* the link drops for about ten minutes on a seeded schedule;
* **`offline` is not `disconnected`** - it means "no contact with the charger",
  never "somebody unplugged the car" (D4 §5.11);
* a command issued while offline is simply lost, with no error anywhere;
* a limit read back is a poll interval old, so the controller that decides
  against its own memory instead of against the read-back decides on fiction.

And one that costs a session: a link that stays down long enough lets the
charger fall back to its own maximum current, and a reconnect while the last
commanded limit was 0 A sometimes ends the session outright.

`Reads.power_w` stays truthful while the link is down, because the house meter
still sees the car: it is `available` that goes false.  A provider must honour
`available`; the runner's meter sums `power_w`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .base import LIMIT_A, Command, Env, Reads, derive_rng
from .ev import AMP_EPS, EvSim

DROPS_PER_DAY = 4
DROP_S = 600.0
READBACK_S = 60.0
FALLBACK_AFTER_S = 300.0
DROP_SESSION_ON_RECONNECT_P = 0.15
OFFLINE = "offline"

SOURCES: dict[str, str] = {
    "DROPS_PER_DAY": (
        "assumed: four link losses a day. D9 §5.9 injects a `ble_flap` fault weekly on top of "
        "this base rate; replaced by a count from the reference house's logbook"
    ),
    "DROP_S": (
        "effektstyring README: the link vanishes with entities left unavailable, on the order of "
        "ten minutes; assumed for the exact figure"
    ),
    "READBACK_S": (
        "effektstyring README: '_verify_write reads every write back a poll interval later'; "
        "assumed: 60 s for that interval"
    ),
    "FALLBACK_AFTER_S": (
        "assumed: after 5 min without contact the charger reverts its dynamic current to its own "
        "maximum — the README's 'the charger may have fallen back to its own 32 A maximum while "
        "we were blind'"
    ),
    "DROP_SESSION_ON_RECONNECT_P": (
        "assumed: 15 % of reconnects with the last limit at 0 A end the session. Replaced by a "
        "rate counted from the reference house's dropped sessions"
    ),
    "AMP_EPS": "see sim/ev.py",
}


@dataclass(slots=True)
class BleChargerSim:
    """An `EvSim` behind a flaky Bluetooth link."""

    ev: EvSim
    seed: int = 0
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))
    link_up: bool = True
    commands_lost: int = 0
    reconnects: int = 0
    fallbacks: int = 0
    _offline_s: float = field(default=0.0)
    _fallen_back: bool = field(default=False)
    _readback: list[tuple[datetime, float]] = field(default_factory=list)

    # -- the link ----------------------------------------------------------- #

    def _drops(self, day_ordinal: int) -> tuple[tuple[float, float], ...]:
        """Drop windows of a local day as (start_s, end_s) after local midnight."""
        rng = derive_rng(self.seed, "ble", day_ordinal)
        starts = sorted(rng.uniform(0.0, 86400.0 - DROP_S) for _ in range(DROPS_PER_DAY))
        return tuple((s, s + DROP_S) for s in starts)

    def offline_at(self, t: datetime) -> bool:
        """Report whether the link is down at `t` - a pure function of the seed."""
        local = t.astimezone(self.tz)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        for day in (midnight - timedelta(days=1), midnight):
            offset = (local - day).total_seconds()
            for start, end in self._drops(day.date().toordinal()):
                if start <= offset < end:
                    return True
        return False

    # -- the transport ------------------------------------------------------ #

    def step(self, dt_s: float, command: Command | None, env: Env) -> Reads:
        """Pass the command through if the link is up, then step the charger."""
        offline = self.offline_at(env.now)
        forwarded = command

        if offline:
            if command is not None:
                self.commands_lost += 1
                forwarded = None
            self._offline_s += dt_s
            if self._offline_s >= FALLBACK_AFTER_S and not self._fallen_back:
                self.ev.limit_a = self.ev.max_a
                self._fallen_back = True
                self.fallbacks += 1
        elif self.link_up is False:
            self.reconnects += 1
            rng = derive_rng(self.seed, "reconnect", self.reconnects)
            if (
                self.ev.paused or self.ev.limit_a <= AMP_EPS
            ) and rng.random() < DROP_SESSION_ON_RECONNECT_P:
                self.ev.sessions_dropped += 1
                self.ev.rearm_in_s = max(self.ev.rearm_in_s, DROP_S)
            self._offline_s = 0.0
            self._fallen_back = False

        self.link_up = not offline
        reads = self.ev.step(dt_s, forwarded, env)

        # The limit the controller can read is one poll interval old.
        self._readback.append((env.now, self.ev.limit_a))
        cutoff = env.now - timedelta(seconds=READBACK_S)
        while len(self._readback) > 1 and self._readback[1][0] <= cutoff:
            self._readback.pop(0)
        reported_limit = self._readback[0][1]

        if offline:
            return Reads(
                power_w=reads.power_w,
                amps=reads.amps,
                available=False,
                status=OFFLINE,
                values={},
            )
        return Reads(
            power_w=reads.power_w,
            amps=reads.amps,
            available=True,
            status=reads.status,
            values={**reads.values, LIMIT_A: reported_limit},
        )
