"""Per-phase currents and headroom, in amps (D3 §2, §4).

A load declares the phases it sits on; one that does not know is bounded by the
tightest phase, which is conservative and correct. Nothing here clamps: negative
headroom is a breach D6 has to act on, not a number to hide.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = ["Phase", "PhaseReadings", "headroom_a"]


class Phase(StrEnum):
    """The phase labels a load may declare (D3 §2)."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


_ORDER = (Phase.L1, Phase.L2, Phase.L3)


@dataclass(frozen=True, slots=True)
class PhaseReadings:
    """The site's per-phase currents at one instant (D3 §4)."""

    amps: tuple[float, ...]
    at: datetime
    limit_a: float

    def headroom_a(self) -> tuple[float, ...]:
        """`limit_a − I` for each phase, in amps."""
        return tuple(self.limit_a - a for a in self.amps)

    def min_headroom_a(self) -> float:
        """Return the tightest phase's headroom - what an unknown-phase load gets."""
        return min(self.headroom_a())


def headroom_a(readings: PhaseReadings, phases: frozenset[str] | None) -> float:
    """Headroom for a load on `phases`; the minimum when it is unknown (D3 §2)."""
    if not phases:
        return readings.min_headroom_a()
    available = readings.headroom_a()
    wanted = [
        available[i]
        for i, label in enumerate(_ORDER)
        if i < len(available) and label.value in phases
    ]
    return min(wanted) if wanted else readings.min_headroom_a()
