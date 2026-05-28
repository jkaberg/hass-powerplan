"""A sub-fuse and the loads behind it (D6 §5.8, INV-60).

    unseen_w             = max(0, sub_meter_w − Σ measured(members))     (0 without a sub-meter)
    cap_w(load ∈ members) = fuse_w − unmetered_w − unseen_w − Σ reserved(other decided members)
    still_w(grants)       = unmetered_w + unseen_w + Σ reserved(members under grants) − fuse_w
    post()                = a blunt Violation for the members when still_w > 0

"The garage circuit is fused at 32 A: the charger and the sauna will never exceed it
together" (D6 §6). Circuits **nest**: they are ordinary constraints and the walk
takes the `min` over all of them, so a 16 A charger circuit inside a 32 A garage
circuit tightens it without either knowing about the other.

The circuit is budgeted the way the site is (D6 §5.3, one level in): what the
members **reserve** under this tick's grants - a relay's nameplate, a charger's
grant - plus what the sub-meter sees that no member's own reading explains, which
is the garage's freezer or a guest's car on the dumb socket. A member walked
earlier is reserved before a later one is capped, so the sauna the household just
lit is charged to the charger before it draws a watt, and the charger yields on
the tick the sauna is granted, not a settle time later. A breach is what the
grants *cannot* resolve - `post()` reads "what is still violated once the grants
are decided" (D6 §2) - so a fuse at 118 % for the seconds a charger takes to back
off is not a stage 4, and the members' own `min_off` never turns a sauna evening
into a 15-minute flap (`design/DECISIONS.md` D-0284, superseding D-0167's raw
sub-meter subtraction). A breach of the fuse is a `fuse_breach` for the circuit's
members only: 32 A of garage says nothing about the other 60 amps.

**WP2.5.** `CircuitSpec` is the circuit as the subentry stores it (D6 §6: fuse in
amps, phases, members, whether a sub-meter is bound, the unmetered allowance) and
`spec.limit(electrical)` is the constraint over it, the fuse converted with the
site's own volts (D3 §5.1). The sub-meter's reading arrives per tick in
`AllocCtx.circuits` and `prepare()` takes it - `None` there is a meter that cannot
answer, and the circuit falls back to the members' own measurements plus the
allowance (D6 §8), which the report says (`CircuitReport.sub_meter`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

from ..report import ShedReason
from ..reserved import MODULATING_KINDS, measured_w, reserved_w
from .base import Violation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...metering import ElectricalProfile
    from ...strategies import LoadView
    from .base import AllocCtx, Scope

__all__ = ["CircuitLimit", "CircuitSpec"]


@dataclass(frozen=True, slots=True)
class CircuitSpec:
    """One circuit as configured (D6 §6): what the subentry says, no reading in it."""

    key: str
    fuse_a: float
    phases: Literal[1, 2, 3]
    members: frozenset[str]
    sub_metered: bool = False
    unmetered_w: float = 0.0
    name: str = ""

    def limit_w(self, electrical: ElectricalProfile) -> float:
        """Return the fuse in watts on this site's supply (D3 §5.1)."""
        return self.fuse_a * electrical.w_per_amp(self.phases)

    def limit(self, electrical: ElectricalProfile) -> CircuitLimit:
        """Return the constraint over this circuit, its reading left to the tick."""
        return CircuitLimit(
            key=self.key,
            limit_w=self.limit_w(electrical),
            members=self.members,
            unmetered_w=self.unmetered_w,
        )


class CircuitLimit:
    """One sub-circuit: its fuse in watts, its members, and how it is measured."""

    scope: ClassVar[Scope] = "circuit"
    shed_reason: ClassVar[ShedReason] = ShedReason.CIRCUIT

    def __init__(
        self,
        *,
        key: str,
        limit_w: float,
        members: frozenset[str],
        sub_meter_w: float | None = None,
        unmetered_w: float = 0.0,
    ) -> None:
        """Build the circuit; `sub_meter_w` is a reading given once, where the tick gives none."""
        self.key = key
        self.limit_w = limit_w
        self.members = members
        self.sub_meter_w = sub_meter_w
        self.unmetered_w = unmetered_w
        self._ctx: AllocCtx | None = None
        self._unseen_w = 0.0

    def prepare(self, ctx: AllocCtx) -> None:
        """Take this tick's context and reading; work out what the sub-meter sees that no member explains."""
        self._ctx = ctx
        if self.key in ctx.circuits:
            self.sub_meter_w = ctx.circuits[self.key]
        self._unseen_w = (
            0.0
            if self.sub_meter_w is None
            else max(0.0, self.sub_meter_w - (self._members_measured_w() or 0.0))
        )

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return what is left under the fuse for a member; `None` for anyone else."""
        if load.load_id not in self.members or self._ctx is None:
            return None
        taken = self._ctx.reserved_of(
            {
                load_id: watts
                for load_id, watts in granted_so_far.items()
                if load_id in self.members
            },
            without=load.load_id,
        )
        return max(0.0, self.limit_w - self.unmetered_w - self._unseen_w - taken)

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return the circuit's breach the grants leave standing, scoped to its members (INV-60)."""
        still = self._still_w(grants)
        if still <= 0.0:
            return ()
        return (
            Violation(
                constraint=self.key,
                scope_id=self.key,
                excess_w=still,
                members=tuple(sorted(self.members)),
                blunt=True,
            ),
        )

    def reserve_w(self) -> float:
        """Return the unmetered load behind the fuse: nobody grants it and it draws."""
        return self.unmetered_w

    def report_row(self, grants: Mapping[str, float]) -> tuple[float | None, float, bool]:
        """Return `(measured, reserved, breached)` for the report (D6 §4)."""
        return self._measured_total(), self._reserved_total(grants), self._still_w(grants) > 0.0

    @property
    def sub_metered(self) -> bool:
        """Whether this tick's figure is the sub-meter's, not the members' sum (D6 §8)."""
        return self.sub_meter_w is not None

    def _still_w(self, grants: Mapping[str, float]) -> float:
        """Return what the fuse is over by once the members draw what they were granted.

        A modulating load will draw its grant; a relay draws what it draws - its
        reservation, or its own reading where that is higher (a dishwasher's
        element pulling 4 kW past a 2.2 kW nameplate is over the fuse whatever
        the nameplate says), and nothing once its grant is zero and it is off.
        """
        if self._ctx is None:
            return self.unmetered_w + self._unseen_w - self.limit_w
        members = 0.0
        for load_id, watts in grants.items():
            if load_id not in self.members:
                continue
            load = self._ctx.load(load_id)
            if load is None:
                continue
            view = self._ctx.view(load_id)
            contribution = reserved_w(load, watts, view)
            if load.kind not in MODULATING_KINDS and watts > 0.0:
                measured = measured_w(view)
                if measured is not None:
                    contribution = max(contribution, measured)
            members += contribution
        return self.unmetered_w + self._unseen_w + members - self.limit_w

    def _members_measured_w(self) -> float | None:
        """Return Σ of what the members measure, settling-aware (D3 §5.8); `None` when none does."""
        if self._ctx is None:
            return None
        known = [
            watts
            for load_id in sorted(self.members)
            if (watts := measured_w(self._ctx.view(load_id))) is not None
        ]
        return sum(known) if known else None

    def _measured_total(self) -> float | None:
        """Return what the circuit draws: the sub-meter, or Σ members, plus unmetered.

        `None` when neither is knowable - a circuit with no sub-meter and no member
        that measures itself cannot say what it draws, and saying so is better than
        inventing a number (D6 §8). `unmetered_w` is what no meter sees, so it is
        added beside either source, exactly as `cap_w` subtracts it (D6 §5.8).
        """
        if self.sub_meter_w is not None:
            return self.sub_meter_w + self.unmetered_w
        members = self._members_measured_w()
        return None if members is None else members + self.unmetered_w

    def _reserved_total(self, grants: Mapping[str, float]) -> float:
        """Return what this circuit's members reserve under `grants`."""
        if self._ctx is None:
            return 0.0
        return self._ctx.reserved_of(
            {load_id: watts for load_id, watts in grants.items() if load_id in self.members}
        )
