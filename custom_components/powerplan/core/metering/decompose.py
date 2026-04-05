"""Splitting the meter reading into the parts the engine reasons about (D3 §5.8).

σ is measured on **uncontrolled** power, never on total: feed it total and our
own shedding inflates σ, which inflates the reserve, which triggers more
shedding - the controller talks itself into a corner and stays there (INV-16).
A load whose write is still settling is counted at what was **commanded**, not at
its lagging sensor (INV-18, the 30-second square wave).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model import Quality

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["ControlledView", "consumption", "controlled_power", "surplus", "uncontrolled"]


@dataclass(frozen=True, slots=True)
class ControlledView:
    """What D3 needs from one controlled load this tick (D3 §4)."""

    load_id: str
    measured_w: float | None
    commanded_w: float | None
    settling: bool
    phases: frozenset[str] | None


def controlled_power(view: ControlledView) -> float:
    """Return the power to attribute to one controlled load (D3 §5.8, INV-18)."""
    if view.settling and view.commanded_w is not None:
        return view.commanded_w
    if view.measured_w is not None:
        return view.measured_w
    return 0.0


def uncontrolled(grid_w: float, views: Iterable[ControlledView]) -> float:
    """Grid power less every controlled load (D3 §5.8, INV-16).

    Signed and unclamped: on a site that exports, uncontrolled power can be
    negative, and pretending otherwise would hide production from σ.
    """
    return grid_w - sum(controlled_power(view) for view in views)


def unmetered(views: Sequence[ControlledView]) -> tuple[str, ...]:
    """Return the loads counted as 0 W because nothing measures or commands them."""
    return tuple(
        view.load_id
        for view in views
        if view.measured_w is None and not (view.settling and view.commanded_w is not None)
    )


def consumption(grid_w: float, production_w: float | None) -> tuple[float, Quality]:
    """Return what the house used: `grid_w + production_w` (D3 §2).

    Unknown while exporting without a production sensor - then it is reported as
    `≥ max(grid_w, 0)` with quality `partial`, never as a confident number.
    """
    if production_w is not None:
        return grid_w + production_w, Quality.OK
    if grid_w < 0.0:
        return 0.0, Quality.PARTIAL
    return grid_w, Quality.OK


def surplus(grid_w: float, battery_charge_w: float) -> float:
    """Export plus whatever a battery is already absorbing (D3 §2).

    What D5's `surplus` strategy may allocate. The caller smooths it; D5 decides
    how long it must persist before acting.
    """
    return max(0.0, -grid_w) + battery_charge_w
