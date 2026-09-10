"""A load the grid switches: no power outside its windows (D6 §5.8, D4 §5.16, G14).

CZ's and SK's HDO: the grid's own relay decides when a water heater or a storage
heater may draw, by the code on its meter's label. Outside the window the relay
is open whatever the allocator grants, so the grant is 0 at every stage - the
constraint is hard-scoped and bounds a comfort floor's grant too (INV-1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..report import ShedReason

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import tzinfo

    from ...pricing import HolidayCalendar
    from ...strategies import LoadView
    from .base import AllocCtx, Scope, Violation

__all__ = ["GridSwitched"]


class GridSwitched:
    """Cap a switched load at 0 outside the windows its `allowed` names."""

    key: ClassVar[str] = "grid_switched"
    scope: ClassVar[Scope] = "switched"
    shed_reason: ClassVar[ShedReason] = ShedReason.GRID_SWITCHED

    def __init__(self, tz: tzinfo, calendar: HolidayCalendar) -> None:
        """Build for the site's zone and holidays, which the windows are read in."""
        self.tz = tz
        self.calendar = calendar
        self._ctx: AllocCtx | None = None

    def prepare(self, ctx: AllocCtx) -> None:
        """Read the tick's time."""
        self._ctx = ctx

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return 0 outside the load's windows; nothing for a load the grid does not switch."""
        if load.allowed is None or self._ctx is None:
            return None
        return None if load.allowed_at(self._ctx.now, self.tz, self.calendar) else 0.0

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return nothing: the grid's relay enforces the window, not a breach."""
        return ()

    def reserve_w(self) -> float:
        """Return 0: the window pins nothing."""
        return 0.0
