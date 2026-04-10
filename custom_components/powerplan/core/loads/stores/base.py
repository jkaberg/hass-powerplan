"""What a load can store, and for how long (D4 §4.3).

Store models are **direction-agnostic**: heating and cooling are one model with
the sign flipped, so pre-cooling before an afternoon demand window is the AU/US
equivalent of the Norwegian night charge. Every model has a maximum, and nothing
asks for energy beyond it (INV-56).

A store is pure physics and holds no reads: which sensor a level comes from is
the device type's sensor-mode answer, so the type passes the level in. That is a
change from D4 §4.3's `level(reads)` and the reason is in
`design/DECISIONS.md` D-0062.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

__all__ = ["StoreCtx", "StoreDirection", "StoreModel", "hours_until"]

#: Which way a store works (D4 §4.3). `both` is a device whose `hvac_modes`
#: include cooling as well as heating.
type StoreDirection = Literal["heat", "cool", "both"]


@dataclass(frozen=True, slots=True)
class StoreCtx:
    """What the world outside the store is doing (D4 §4.3).

    `outdoor_c` comes from a bound sensor or the site's weather forecast (D10);
    `indoor_c` from the load's own room. Both may be missing, and a loss term
    nobody can compute is **skipped**, never guessed - a store that overstates
    what it needs charges at the wrong hour and never notices.
    """

    now: datetime
    outdoor_c: float | None = None
    indoor_c: float | None = None


def hours_until(deadline: datetime | None, now: datetime) -> float:
    """Hours from `now` to `deadline`, 0 when it is absent or already past."""
    if deadline is None:
        return 0.0
    return max(0.0, (deadline - now).total_seconds() / 3600.0)


class StoreModel(Protocol):
    """What a load can bank, in the unit it is measured in (D4 §4.3)."""

    @property
    def direction(self) -> StoreDirection:
        """Which way this store works; a frozen field satisfies a read-only one."""
        ...

    def required_kwh(
        self, level_now: float | None, target: float, deadline: datetime | None, ctx: StoreCtx
    ) -> float | None:
        """Energy to reach `target` by `deadline`, including loss until then.

        `None` when it cannot be computed - an unknown level is not a zero.
        """
        ...

    def max_level(self) -> float:
        """Return the ceiling no fill may pass (INV-56)."""
        ...

    def min_level(self) -> float:
        """Return the floor the hardware or the household imposes."""
        ...

    def coast_hours(self, level_now: float, floor: float, ctx: StoreCtx) -> float | None:
        """How long before the store falls to `floor`, or `None` when unfitted."""
        ...

    def capacity_kwh_per_unit(self) -> float:
        """KWh per kelvin, or per percentage point of SoC."""
        ...
