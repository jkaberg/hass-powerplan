"""The site's hard limits - item 1 of the precedence (D6 §5.8, INV-1).

Three of them, and they are all the same shape: a watt ceiling on the whole site
that bounds **everything**, a comfort violator's grant included (HLD §3). They are
physical or contractual facts, never capacity numbers, so their violations are
blunt and a capacity step may never be passed to one (INV-36).

    SiteFuse               the main fuse. Exceeding it opens a breaker.
    ContractedPowerLimit   D2's `limit_now_w` - the meter's own relay or a surcharge.
    ExternalLimit          a DSO event: §14a's `max_w = 4200` for the named loads
                           while it is active, or a site-wide cap on `P_hard`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..report import ShedReason
from .base import Violation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...strategies import LoadView
    from ...tariffs import HardLimit
    from .base import AllocCtx, Scope

__all__ = ["ContractedPowerLimit", "ExternalLimit", "SiteFuse"]


class _SiteLimit:
    """The shared body: a watt ceiling on the site, less what the site already holds.

    A load is judged against the limit minus the **uncontrolled** baseline and minus
    what the loads decided before it reserve - its own reservation is never among
    them, because the walk has not decided it yet (D6 §5.3).
    """

    key: ClassVar[str] = "site"
    scope: ClassVar[Scope] = "site"
    shed_reason: ClassVar[ShedReason] = ShedReason.BUDGET

    def __init__(self, limit_w: float | None) -> None:
        """Build the limit; `None` is "no opinion this tick"."""
        self._limit_w = limit_w
        self._uncontrolled_w = 0.0
        self._ctx: AllocCtx | None = None

    def prepare(self, ctx: AllocCtx) -> None:
        """Read the uncontrolled baseline this tick (D3 §5.8)."""
        self._ctx = ctx
        self._uncontrolled_w = ctx.meter.uncontrolled_w or 0.0

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return what is left under the limit for `load`."""
        if self._limit_w is None or self._ctx is None:
            return None
        taken = self._ctx.reserved_of(granted_so_far, without=load.load_id)
        return max(0.0, self._limit_w - self._uncontrolled_w - taken)

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return nothing: a breached **site** limit is the ladder's, not a scope.

        `fuse_breach` and `trip_risk` are blunt ladder reasons (D6 §5.4) and stage 4
        already reaches every load; a `Violation` here would repeat that, scoped to
        nothing narrower. Violations exist to scope a breach to a *subset* - a
        circuit, a phase, an event's named loads (INV-60).
        """
        return ()

    def reserve_w(self) -> float:
        """Return 0: a site limit pins nothing, it only bounds."""
        return 0.0


class SiteFuse(_SiteLimit):
    """The main fuse in watts - a physical fact about the house (D6 §5.4)."""

    key: ClassVar[str] = "site_fuse"


class ContractedPowerLimit(_SiteLimit):
    """D2's contracted power in force now, item 1 of the precedence (D2 §5.8)."""

    key: ClassVar[str] = "contracted_power"

    def __init__(self, limit: HardLimit | None) -> None:
        """Build from D2's `limit_now_w`; `None` where no period matches."""
        super().__init__(None if limit is None else limit.w)
        self.limit = limit


class ExternalLimit:
    """A DSO or aggregator event capping named loads, or the site (D6 §5.8).

    Norway's §14a and the Intelligent Octopus posture: while the event is active the
    named loads may take `max_w` and no more. A site-wide event has no `loads` and
    caps everything - it is item 1, so it bounds comfort too.
    """

    key: ClassVar[str] = "external_limit"
    scope: ClassVar[Scope] = "site"
    shed_reason: ClassVar[ShedReason] = ShedReason.EXTERNAL_LIMIT

    def __init__(
        self,
        max_w: float,
        *,
        loads: frozenset[str] = frozenset(),
        active: bool = True,
        event_id: str = "load_limit",
    ) -> None:
        """Build the event's cap; empty `loads` makes it site-wide."""
        self.max_w = max_w
        self.loads = loads
        self.active = active
        self.event_id = event_id

    def prepare(self, ctx: AllocCtx) -> None:
        """Nothing to measure: an event is a statement, not a reading."""

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return `max_w` for a named load while the event is active."""
        if not self.active:
            return None
        if self.loads and load.load_id not in self.loads:
            return None
        return self.max_w

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return a blunt violation for every named load still above the cap."""
        if not self.active:
            return ()
        over = tuple(
            sorted(
                load_id
                for load_id, watts in grants.items()
                if (not self.loads or load_id in self.loads) and watts > self.max_w
            )
        )
        if not over:
            return ()
        excess = max(grants[load_id] - self.max_w for load_id in over)
        return (
            Violation(
                constraint=self.key,
                scope_id=self.event_id,
                excess_w=excess,
                members=over,
                blunt=True,
            ),
        )

    def reserve_w(self) -> float:
        """Return 0: the event pins nothing."""
        return 0.0
