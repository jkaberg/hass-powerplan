"""Target step-ups and arrivals → `(deadline, required_kwh)` (D5 §2, D4 §5.8).

A thermal load's deadline is not a departure time somebody typed: it is the next
moment the household expects the room to be at temperature. `TargetProfile`
answers *when* and *to what* (`deadlines()`, D4 §5.8); the store model answers
*how much*; this module is the one line between them, and each pair becomes a
`deadline_fill` sub-plan.

**The level at the deadline is predicted, not assumed.** A slab at 22 °C now will
not still be at 22 °C at 06:00, so the requirement is computed from where the
store will *be* - from the coast model, linearly between now and the floor it is
heading for. With no fitted coast model the current level is used instead, which
understates the need rather than inventing a number: a conservative fill charges
again next slot, an invented one charges at the wrong hour and nobody finds out
(D4's `stores/thermal.py`, the same rule as its loss term).

Nothing here clamps the target: the store does, at its own maximum, which is why
a schedule asking for 40 °C on a slab capped at 27 never asks for more than the
slab can hold (INV-56).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from ..loads import PresenceMode

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..loads import CalendarEvent, TargetProfile
    from ..loads.stores.base import StoreCtx, StoreModel

__all__ = ["DeadlineNeed", "first", "needs"]


@dataclass(frozen=True, slots=True)
class DeadlineNeed:
    """One `(deadline, target, required_kwh)` a strategy must plan for (D5 §2)."""

    at: datetime
    target: float
    required_kwh: float
    source: Literal["schedule", "arrival"]

    @property
    def reason(self) -> str:
        """The line the review sensor shows for this sub-plan."""
        return f"{self.target:.1f} by {self.at:%H:%M} ({self.source})"


def needs(
    profile: TargetProfile,
    store: StoreModel,
    *,
    level_now: float | None,
    from_: datetime,
    until: datetime,
    ctx: StoreCtx,
    presence: PresenceMode = PresenceMode.HOME,
    calendar: Sequence[CalendarEvent] = (),
) -> tuple[DeadlineNeed, ...]:
    """Return every step-up and arrival in `[from_, until)` with its energy.

    Empty when the level is unknown - an unknown level is not a zero, and a plan
    built on a guessed temperature is worse than no plan (D4 §4.3). Under
    `vacation` the schedule's own step-ups are not deadlines (D4 §5.8); an arrival
    still is, and `TargetProfile.deadlines()` is what knows the difference.
    """
    if level_now is None:
        return ()

    arrivals = {event.start for event in calendar if from_ <= event.start < until}
    out: list[DeadlineNeed] = []
    for at, target in profile.deadlines(from_, until, presence, calendar):
        predicted = _level_at(store, level_now, profile.floor, at, ctx)
        required = store.required_kwh(predicted, target, at, ctx)
        if required is None or required <= 0.0:
            continue
        out.append(
            DeadlineNeed(
                at=at,
                target=target,
                required_kwh=required,
                source="arrival" if at in arrivals else "schedule",
            )
        )
    return tuple(out)


def first(found: Sequence[DeadlineNeed]) -> DeadlineNeed | None:
    """Return the earliest need, which is the one a single fill plans for.

    `heat_capacitor` is the union of every sub-plan; a `deadline_fill`
    load has one deadline at a time, and the earliest is the binding one.
    """
    return min(found, key=lambda need: need.at) if found else None


def _level_at(
    store: StoreModel,
    level_now: float,
    floor: float,
    at: datetime,
    ctx: StoreCtx,
) -> float:
    """Return where the store will be at `at` if nothing charges it (D5 §2)."""
    coast_h = store.coast_hours(level_now, floor, ctx)
    if coast_h is None or coast_h <= 0.0:
        return level_now
    hours = max(0.0, (at - ctx.now).total_seconds() / 3600.0)
    travelled = (level_now - floor) * min(1.0, hours / coast_h)
    return level_now - travelled
