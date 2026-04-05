"""Blindness, and how it is reported (D3 §5.10, INV-17).

A stale meter freezes the tick; blindness never opens a gate. Three independent
ways to be blind and all of them count: the reading is missing, the reading is
outside the physically possible, or the reading is too old.

`AnchorKind` lives here rather than in `window.py` so that `MeterHealth` - which
carries it - stays a leaf of the package's import graph.
"""

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["AnchorKind", "MeterHealth", "stale_threshold_s"]

# The floor and the cap on the staleness threshold (D3 §5.4 step 3). A 1 s meter
# must not be called stale after 3 s, and a meter with a slow cadence must not
# buy indefinite blindness.
STALE_FLOOR_S = 30.0
STALE_CAP_S = 300.0


class AnchorKind(StrEnum):
    """Where the window's energy came from, in precedence order (D3 §2, §4)."""

    METER_WINDOW = "meter_window"
    REGISTER_LATCHED = "register_latched"
    REGISTER_INTERPOLATED = "register_interpolated"
    WALL_CLOCK = "wall_clock"


def stale_threshold_s(cadence_s: float | None, max_stale_factor: float) -> float:
    """How old a power reading may be before the tick freezes (D3 §5.4 step 3)."""
    if cadence_s is None:
        return STALE_FLOOR_S
    return max(STALE_FLOOR_S, min(STALE_CAP_S, max_stale_factor * cadence_s))


@dataclass(frozen=True, slots=True)
class MeterHealth:
    """Why the meter can or cannot be trusted this tick (D3 §4, §5.10)."""

    power_age_s: float | None
    register_age_s: float | None
    stale: bool
    degraded: bool
    implausible_count: int
    register_cadence_s: float | None
    integral_bias_w: float | None
    anchor_kind: AnchorKind
    production_known: bool
    unmetered_controlled: tuple[str, ...] = ()
