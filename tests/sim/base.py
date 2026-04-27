"""Shapes the physics simulators share (D9 §3, §4).

Owned by `tests/sim`: nothing here imports `custom_components`, so a simulator
does not move when the core's types move.  WP0.9's runner adapts between these
and `core.model` (D-0041).

Two shapes, because a simulated house has two kinds of thing in it:

* `SimLoad` - a device.  `step(dt_s, command, env) -> Reads` drives physics
  forward by `dt_s` seconds under `command` in ambient conditions `env`.
* `SimSource` - a generator (weather, prices, the household, the uncontrolled
  load).  A pure function of `t` given the seed, so the planner may look ahead
  and two runs in any order agree byte for byte.

Units follow the project's convention: W (signed, import +, export −), kWh, A,
°C.  Every `datetime` is tz-aware; arithmetic is in UTC; local time appears only
where a household or a tariff boundary makes it real.

Provenance (D9 §2): every module here carries a `SOURCES` mapping from the name
of each numeric module constant to where the number comes from - a URL, a
document title, or `assumed: <what would replace it>`.  `test_sources.py`
enforces that the mapping is complete, so a value without a source fails.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------- #
# Electrical arithmetic (D3 §5.1)
# --------------------------------------------------------------------------- #

SQRT3 = 1.7320508075688772
#: W per A on a three-phase load in a 230 V IT net - √3 × 230 (D3 §5.1).
W_PER_AMP_IT230_3P = SQRT3 * 230.0
#: W per A on a single-phase load in a 230 V IT net - line to line (D3 §5.1).
W_PER_AMP_IT230_1P = 230.0

SECONDS_PER_HOUR = 3600.0
QUARTER_S = 900.0

SOURCES: dict[str, str] = {
    "SQRT3": "mathematics",
    "W_PER_AMP_IT230_3P": "D3 §5.1 unit conversion table (IT_230, 3 phases)",
    "W_PER_AMP_IT230_1P": "D3 §5.1 unit conversion table (IT_230, 1 phase)",
    "SECONDS_PER_HOUR": "SI",
    "QUARTER_S": "15 min — the slot length D1/D3 use",
}


def kwh(power_w: float, dt_s: float) -> float:
    """Energy in kWh from a constant `power_w` held for `dt_s` seconds."""
    return power_w * dt_s / (SECONDS_PER_HOUR * 1000.0)


def amps_3p(power_w: float) -> tuple[float, float, float]:
    """Per-phase amps for a balanced three-phase load in a 230 V IT net."""
    a = power_w / W_PER_AMP_IT230_3P
    return (a, a, a)


def amps_1p(power_w: float, phase: int = 0) -> tuple[float, float, float]:
    """Per-phase amps for a single-phase load on `phase` (0, 1 or 2)."""
    out = [0.0, 0.0, 0.0]
    out[phase] = power_w / W_PER_AMP_IT230_1P
    return (out[0], out[1], out[2])


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def derive_rng(seed: int, *key: object) -> random.Random:
    """Build a `random.Random` from `seed` and `key`, stable across runs.

    Python salts `hash()` for strings, so the key is hashed with blake2b
    instead: the same seed and key give the same stream in every process, which
    is what makes a generator a pure function of `t` (D9 §8 - a flaky scenario
    is a bug, not a retry).
    """
    digest = hashlib.blake2b(repr(key).encode(), digest_size=8).digest()
    return random.Random(seed ^ int.from_bytes(digest, "big"))


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC instants of local midnight on `day` and on the next local day.

    On a DST day the span is 23 h or 25 h, which is where the 92/100-slot days
    come from.
    """
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    nxt = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start, nxt.astimezone(UTC)


def quarter_slots(day: date, tz: ZoneInfo) -> tuple[datetime, ...]:
    """UTC starts of every 15-minute slot of the local day `day`.

    96 on an ordinary day, 92 on the spring-forward day, 100 on the fall-back
    day.
    """
    start, end = local_day_bounds(day, tz)
    out: list[datetime] = []
    t = start
    step = timedelta(seconds=QUARTER_S)
    while t < end:
        out.append(t)
        t += step
    return tuple(out)


# --------------------------------------------------------------------------- #
# What crosses the boundary
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Env:
    """Ambient conditions at one instant - everything a device does not own."""

    now: datetime
    outdoor_c: float
    solar_w_per_m2: float = 0.0
    ground_c: float = 8.0
    cold_water_c: float = 8.0
    occupants: int = 0


@dataclass(frozen=True, slots=True)
class Command:
    """One write to a device, in the vocabulary of D4's four control kinds.

    `limit_a` is MODULATE (amps, never watts), `setpoint_c` is SETPOINT, `mode`
    is MODE, `on` is SWITCH and the deliberate pause, `start` is the
    appliance-cycle START role.  A field left `None` says nothing about that
    axis: the device keeps what it had.
    """

    on: bool | None = None
    limit_a: float | None = None
    setpoint_c: float | None = None
    mode: str | None = None
    start: bool = False


@dataclass(frozen=True, slots=True)
class Reads:
    """What a provider would read back from the device after a step.

    `power_w` is signed (import +, export −); `amps` is per phase.  Anything
    device-specific goes in `values` under the keys below, because a union of
    twelve device shapes buys nothing a named key does not.
    """

    power_w: float
    amps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    available: bool = True
    status: str | None = None
    values: Mapping[str, float] = field(default_factory=dict)
    #: When a value was *taken*, for the ones a poll delivers late - what an HA
    #: entity carries as `last_reported`. A value with no stamp is as old as the
    #: step that produced it.
    stamps: Mapping[str, datetime] = field(default_factory=dict)


# `Reads.values` keys.  A device reports the subset it has a sensor for.
TEMP_FLOOR = "temp_floor"
TEMP_AIR = "temp_air"
TEMP_TOP = "temp_top"
TEMP_BOTTOM = "temp_bottom"
TEMP_OUTLET = "temp_outlet"
SETPOINT_C = "setpoint_c"
LIMIT_A = "limit_a"
SOC = "soc"
ENERGY_KWH = "energy_kwh"
REGISTER_IMPORT_KWH = "register_import_kwh"
REGISTER_EXPORT_KWH = "register_export_kwh"
CYCLE_MINUTE = "cycle_minute"
COP = "cop"


@runtime_checkable
class SimLoad(Protocol):
    """A device the controller writes to (D9 §4)."""

    def step(self, dt_s: float, command: Any, env: Env) -> Reads:
        """Advance the physics `dt_s` seconds under `command` and report."""
        ...


class SimSource[T](Protocol):
    """A time-indexed generator: a pure function of `t` given its seed."""

    seed: int

    def at(self, t: datetime) -> T:
        """Return the generated value at instant `t`."""
        ...
