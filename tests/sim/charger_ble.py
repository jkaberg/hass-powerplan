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
* a limit read back is what the last poll saw, up to a poll interval old and
  stamped with the poll's time, so the controller that decides against its own
  memory instead of against the read-back decides on fiction.

And one that costs a session: a link that stays down long enough lets the
charger fall back to its own maximum current, and a reconnect while the last
commanded limit was 0 A sometimes ends the session outright.

`Reads.power_w` stays truthful while the link is down, because the house meter
still sees the car: it is `available` that goes false.  A provider must honour
`available`; the runner's meter sums `power_w`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .base import LIMIT_A, Command, Env, Reads, derive_rng
from .ev import AMP_EPS, EvSim

DROPS_PER_DAY = 4
DROP_S = 600.0
READBACK_S = 30.0
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
        "D4 §5.10 profile table: `easee_ble` polls every 30 s, and the effektstyring README's "
        "'_verify_write reads every write back a poll interval later' is that poll. The poll "
        "grid's phase is seeded; a poll at the instant of a write sees the charger before it"
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
    #: An injected loss of contact (D9 §4 `ble_flap`): offline until this instant,
    #: whatever the seeded schedule says. A flap is a loss of *contact*, not a
    #: reconnect a tick - forcing `link_up` off from outside rolled the
    #: reconnect dice every ten seconds and dropped a session a day.
    offline_until: datetime | None = None
    commands_lost: int = 0
    reconnects: int = 0
    fallbacks: int = 0
    _offline_s: float = field(default=0.0)
    _fallen_back: bool = field(default=False)
    _last_step_at: datetime | None = field(default=None)
    _polled: tuple[datetime, float] | None = field(default=None)

    # -- the link ----------------------------------------------------------- #

    def _drops(self, day_ordinal: int) -> tuple[tuple[float, float], ...]:
        """Drop windows of a local day as (start_s, end_s) after local midnight."""
        rng = derive_rng(self.seed, "ble", day_ordinal)
        starts = sorted(rng.uniform(0.0, 86400.0 - DROP_S) for _ in range(DROPS_PER_DAY))
        return tuple((s, s + DROP_S) for s in starts)

    def offline_at(self, t: datetime) -> bool:
        """Report whether the link is down at `t`: the seeded schedule, or an injected flap."""
        if self.offline_until is not None and t < self.offline_until:
            return True
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
        before_a = self.ev.limit_a
        reads = self.ev.step(dt_s, forwarded, env)

        # The limit the controller can read is what the last poll saw. A poll
        # that falls inside this step saw the charger *before* this step's
        # command landed; a step with no poll in it leaves the entity as it was.
        poll_at = self._poll_in(env.now, dt_s)
        if poll_at is not None:
            self._polled = (poll_at, before_a)
        self._last_step_at = env.now
        if self._polled is None:
            self._polled = (env.now - timedelta(seconds=dt_s), before_a)
        polled_at, reported_limit = self._polled

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
            stamps={LIMIT_A: polled_at},
        )

    def _poll_in(self, now: datetime, dt_s: float) -> datetime | None:
        """Return the last poll instant inside `(previous step, now]`, if any."""
        previous = self._last_step_at
        if previous is None:
            previous = now - timedelta(seconds=dt_s)
        phase_s = derive_rng(self.seed, "ble_poll").uniform(0.0, READBACK_S)
        now_s = now.timestamp()
        latest_s = math.floor((now_s - phase_s) / READBACK_S) * READBACK_S + phase_s
        if latest_s <= previous.timestamp():
            return None
        return datetime.fromtimestamp(latest_s, tz=now.tzinfo)
