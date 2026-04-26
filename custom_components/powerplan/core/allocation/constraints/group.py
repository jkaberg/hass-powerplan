"""A group of loads that share a cap, and the rotation that shares it (D6 §5.6, INV-41).

"Six floor loops share 2 kW when the hour gets tight; the coldest gets it first"
(D6 §6). Five bathroom loops all being cold at once is what rotation exists to
prevent, and the four rules that prevent it are:

    rotation_active = stage ≥ from_stage (1) OR projected ≥ 0.85 × ceiling
    eligible        = members below their comfort TARGET that want power
    order           = deficit descending; a member held back past 30 min jumps
    admission       = admit while Σ nameplate ≤ cap, STOP at the first non-fit;
                      the top-ranked member is admitted even alone above the cap

**Below that threshold the group makes no decision at all.** A group that rotates
in a free hour is a group that cycles relays for nothing, and the relay is the part
that wears out.

**The ranking is against the target, not the floor** (INV-41). The floor is the
line the allocator breaches the ceiling to defend (HLD §3) and every member is
normally above it; what says which room is coldest is how far each is from where
the household asked it to be.

**Admission stops at the first member that does not fit.** Walking on to a smaller
one looks like better packing and is how the second-coldest loop waits behind
whichever members happen to be cheap to admit - the starvation the clock then has
to undo. The one exception is the top of the queue: a loop rated above its own
group's cap would otherwise be excluded for ever, so it is admitted alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ...pricing.model import Field, FieldKind, Schema
from ..report import RotationReport, ShedReason

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from ...strategies import LoadView
    from .base import AllocCtx, Scope, Violation

__all__ = [
    "DEFAULT_CEILING_FRACTION",
    "DEFAULT_FROM_STAGE",
    "DEFAULT_STARVE_SECONDS",
    "GroupCap",
    "default_max_concurrent_w",
]

#: From which ladder stage the group starts rationing (D6 §6).
DEFAULT_FROM_STAGE = 1

#: The projection, as a fraction of the ceiling, at which the group rations even at
#: stage 0: the hour is already tight, and waiting for the stage wastes the half of
#: the window in which shifting a loop still changes the outcome.
DEFAULT_CEILING_FRACTION = 0.85

#: How long a member may be held back before it jumps the queue, in seconds.
DEFAULT_STARVE_SECONDS = 1800.0

#: Absorbs the float round trip in the admission sum.
_EPS_W = 1e-6


def default_max_concurrent_w(nameplates: Iterable[float]) -> float:
    """Return D6 §6's default cap: the two largest members' nameplates summed.

    The derivation the group flow offers, so no number is invented in the UI: two
    of six loops at a time is what a 2 kW cap means for the reference house, and a
    group of one is just that load.
    """
    largest = sorted(nameplates, reverse=True)[:2]
    return float(sum(largest))


class GroupCap:
    """One group of loads sharing `max_concurrent_w`, rationed by who is cold."""

    scope: ClassVar[Scope] = "group"
    shed_reason: ClassVar[ShedReason] = ShedReason.GROUP_CAP
    schema: ClassVar[Schema] = (
        Field("name", FieldKind.TEXT, required=True),
        Field("members", FieldKind.LIST, required=True),
        Field("max_concurrent_w", FieldKind.NUMBER, required=True, unit="W"),
        Field("from_stage", FieldKind.NUMBER, default=DEFAULT_FROM_STAGE, advanced=True),
        Field(
            "ceiling_fraction",
            FieldKind.NUMBER,
            default=DEFAULT_CEILING_FRACTION,
            unit="fraction",
            advanced=True,
        ),
        Field(
            "starve_seconds",
            FieldKind.NUMBER,
            default=DEFAULT_STARVE_SECONDS,
            unit="s",
            advanced=True,
        ),
    )

    def __init__(
        self,
        *,
        key: str,
        members: frozenset[str],
        max_concurrent_w: float,
        from_stage: int = DEFAULT_FROM_STAGE,
        ceiling_fraction: float = DEFAULT_CEILING_FRACTION,
        starve_seconds: float = DEFAULT_STARVE_SECONDS,
        starved_since: Mapping[str, datetime] | None = None,
    ) -> None:
        """Build the group; `starved_since` is what `AllocState` remembered (D6 §7)."""
        self.key = key
        self.members = members
        self.max_concurrent_w = max_concurrent_w
        self.from_stage = from_stage
        self.ceiling_fraction = ceiling_fraction
        self.starve_seconds = starve_seconds
        self._starved_since: Mapping[str, datetime] = dict(starved_since or {})
        self._active = False
        self._reason = "no scarcity"
        self._queue: tuple[tuple[str, float, float], ...] = ()
        self._chosen: tuple[str, ...] = ()
        self._demand_w = 0.0

    # ------------------------------------------------------------- the protocol #

    def seed(self, starved_since: Mapping[str, datetime]) -> None:
        """Take last tick's rotation clocks for this group's members (D6 §7).

        The allocator calls it with `AllocState.starved_since` before `prepare`;
        a group built with its own `starved_since` (tests, diagnostics) keeps it.
        """
        if self._starved_since:
            return
        self._starved_since = {
            load_id: since for load_id, since in starved_since.items() if load_id in self.members
        }

    def prepare(self, ctx: AllocCtx) -> None:
        """Rank the members and decide who is admitted this tick (D6 §5.6)."""
        self._active, self._reason = self._scarcity(ctx)
        self._queue = self._rank(ctx)
        self._demand_w = sum(nameplate for _lid, _deficit, nameplate in self._queue)
        self._chosen = self._admit() if self._active else tuple(lid for lid, _d, _n in self._queue)
        self._starved_since = self._clocks(ctx)

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return 0 W for a member rotation is holding back, `None` for anyone else.

        `None` for an admitted member: admission is by nameplate, so the cap is
        already carried by *who* is in the set - a second, per-load figure would
        bind the same watts twice.
        """
        if not self._active or load.load_id not in self.members:
            return None
        return None if load.load_id in self._chosen else 0.0

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return nothing: a group cap is a preference, and preferences never breach.

        Item 5 of the precedence (INV-1). A `Violation` is how a *physical* limit
        scopes a blunt stage 4 to its own members (INV-60); a group that is over its
        cap has simply been out-ranked by a comfort floor, which is allowed to.
        """
        return ()

    def reserve_w(self) -> float:
        """Return 0: a group pins no power of its own."""
        return 0.0

    # --------------------------------------------------------------- the report #

    def starvation(self) -> Mapping[str, datetime]:
        """Return the starvation clocks for the next tick (D6 §7)."""
        return self._starved_since

    def report(self) -> RotationReport:
        """Return what this group decided, for `AllocReport.rotation` (D6 §4)."""
        return RotationReport(
            active=self._active,
            reason=self._reason,
            cap_w=self.max_concurrent_w,
            chosen=self._chosen if self._active else (),
            demand_w=self._demand_w,
            queue=self._queue,
        )

    # -------------------------------------------------------------- internals #

    def _scarcity(self, ctx: AllocCtx) -> tuple[bool, str]:
        """Return whether the group rations this tick, and why (D6 §5.6)."""
        if ctx.stage >= self.from_stage:
            return True, "stage"
        ceiling = ctx.budget.ceiling_kwh
        if ctx.budget.eligible and ctx.budget.projected_kwh >= self.ceiling_fraction * ceiling:
            return True, "projection"
        return False, "no scarcity"

    def _rank(self, ctx: AllocCtx) -> tuple[tuple[str, float, float], ...]:
        """Return the eligible members as `(load_id, deficit, nameplate)`, coldest first.

        A member held back past `starve_seconds` jumps the whole queue: without it
        the warmest loop in a group whose cap fits two of six is never admitted, and
        "the coldest first" becomes "the same two, always".
        """
        rows = [
            (load.load_id, _deficit(load), load.nameplate_w)
            for load in ctx.loads
            if load.load_id in self.members and self._eligible(load)
        ]
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    -self._starved_for(row[0], ctx.now),
                    -row[1],
                    row[0],
                ),
            )
        )

    def _eligible(self, load: LoadView) -> bool:
        """Whether a member is in the running: it wants power and it is not at target.

        An unknown level counts as eligible - blindness never opens a gate (INV-15's
        spirit): a loop whose thermostat has stopped reporting is ranked last, not
        exempted from the cap.
        """
        if not load.demand.wants:
            return False
        comfort = load.demand.comfort
        if comfort is None or comfort.current is None:
            return True
        return comfort.deficit > 0.0

    def _starved_for(self, load_id: str, now: datetime) -> float:
        """Return how long this member has been held back past the clock, in seconds."""
        since = self._starved_since.get(load_id)
        if since is None:
            return 0.0
        held = (now - since).total_seconds()
        return held if held >= self.starve_seconds else 0.0

    def _admit(self) -> tuple[str, ...]:
        """Walk the queue and admit while the nameplates fit (D6 §5.6)."""
        chosen: list[str] = []
        total = 0.0
        for load_id, _deficit_k, nameplate in self._queue:
            if not chosen:
                # The top of the queue is admitted even alone above the cap, or a
                # loop rated above its own group's cap is excluded for ever (D6 §8).
                chosen.append(load_id)
                total += nameplate
                continue
            if total + nameplate > self.max_concurrent_w + _EPS_W:
                break
            chosen.append(load_id)
            total += nameplate
        return tuple(chosen)

    def _clocks(self, ctx: AllocCtx) -> Mapping[str, datetime]:
        """Return the starvation clocks after this tick's admission (D6 §5.6).

        Rebuilt from the members present, so a group whose loads have gone keeps no
        clock, and an admitted member's clock is cleared the moment it gets its turn.
        """
        clocks: dict[str, datetime] = {}
        if not self._active:
            return clocks
        for load_id, _deficit_k, _nameplate in self._queue:
            if load_id in self._chosen:
                continue
            clocks[load_id] = self._starved_since.get(load_id, ctx.now)
        return clocks


def _deficit(load: LoadView) -> float:
    """Return how far this member is from its comfort **target** (INV-41).

    `ComfortState.deficit` is the one place the presence-folded target already is
    (D4 §4.1, §5.8), and it is signed by direction, so `> 0` means "wants energy"
    for a cooling zone as much as a heating one. A member with no comfort state
    ranks 0 - it is in the queue because it wants power, not because it is cold.
    """
    comfort = load.demand.comfort
    return 0.0 if comfort is None else comfort.deficit
