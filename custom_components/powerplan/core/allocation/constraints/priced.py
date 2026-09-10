"""A limit whose excess is priced - no hard limit (D6 v0.1.14, O23, INV-36 narrowed).

LU's reference power, SI's agreed power: crossing costs a surcharge and trips
nothing. A load with a plan is bounded by its plan's envelope, which already
priced the crossing as D5's power tier (INV-30); a load with **no** plan is
capped so the site stays at the limit, because nothing priced crossing it. It is
scoped to the load, not the site, so a comfort floor's hard-only cap never sees
it, and a breach raises no stage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..report import ShedReason

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...strategies import LoadView
    from ...tariffs import PricedLimit
    from .base import AllocCtx, Scope, Violation

__all__ = ["PricedLimitCap"]


class PricedLimitCap:
    """Cap a load without a plan at what keeps the site under the priced limit."""

    key: ClassVar[str] = "priced_limit"
    scope: ClassVar[Scope] = "load"
    shed_reason: ClassVar[ShedReason] = ShedReason.BUDGET

    def __init__(self, priced: PricedLimit | None) -> None:
        """Build from D2's `priced_limit_now`; `None` where none is in force."""
        self.priced = priced
        self._ctx: AllocCtx | None = None
        self._uncontrolled_w = 0.0

    def prepare(self, ctx: AllocCtx) -> None:
        """Read the tick: the plans and the uncontrolled baseline."""
        self._ctx = ctx
        self._uncontrolled_w = ctx.meter.uncontrolled_w or 0.0

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return nothing for a planned load; for any other, what is left under the limit."""
        if self.priced is None or self._ctx is None:
            return None
        if self._ctx.plans.get(load.load_id) is not None:
            return None
        taken = self._ctx.reserved_of(granted_so_far, without=load.load_id)
        return max(0.0, self.priced.w - self._uncontrolled_w - taken)

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return nothing: exceeding a priced limit is a cost, never a violation (O23)."""
        return ()

    def reserve_w(self) -> float:
        """Return 0: the limit pins nothing."""
        return 0.0
