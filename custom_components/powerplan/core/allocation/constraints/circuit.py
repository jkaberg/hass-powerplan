"""A sub-fuse and the loads behind it (D6 §5.8, INV-60).

    cap_w(load ∈ members) = fuse_w − (sub_meter_w − the load's own draw
                                      if there is a sub-meter
                                      else Σ reserved(other decided members))
                                   − unmetered_w

"The garage circuit is fused at 32 A: the charger and the sauna will never exceed it
together" (D6 §6). Circuits **nest**: they are ordinary constraints and the walk
takes the `min` over all of them, so a 16 A charger circuit inside a 32 A garage
circuit tightens it without either knowing about the other.

A sub-meter reads the circuit **including the asking load**, so the load gives its
own draw back before it is judged - the same rule as `P_free` at site level, and for
the same reason (D6 §5.3). A breach of the fuse is a `fuse_breach` for
the circuit's members only: 32 A of garage says nothing about the other 60 amps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..report import ShedReason
from ..reserved import measured_w
from .base import Violation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...strategies import LoadView
    from .base import AllocCtx, Scope

__all__ = ["CircuitLimit"]


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
        """Build the circuit; `sub_meter_w` is this tick's reading where there is one."""
        self.key = key
        self.limit_w = limit_w
        self.members = members
        self.sub_meter_w = sub_meter_w
        self.unmetered_w = unmetered_w
        self._ctx: AllocCtx | None = None

    def prepare(self, ctx: AllocCtx) -> None:
        """Keep this tick's context: the members' meter rows and reservations."""
        self._ctx = ctx

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return what is left under the fuse for a member; `None` for anyone else."""
        if load.load_id not in self.members or self._ctx is None:
            return None
        if self.sub_meter_w is not None:
            own = measured_w(self._ctx.view(load.load_id)) or 0.0
            taken = max(0.0, self.sub_meter_w - own)
        else:
            taken = self._ctx.reserved_of(
                {
                    load_id: watts
                    for load_id, watts in granted_so_far.items()
                    if load_id in self.members
                },
                without=load.load_id,
            )
        return max(0.0, self.limit_w - taken - self.unmetered_w)

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return the circuit's breach, scoped to its members (INV-60)."""
        measured = self._measured_total()
        if measured is None or measured <= self.limit_w:
            return ()
        return (
            Violation(
                constraint=self.key,
                scope_id=self.key,
                excess_w=measured - self.limit_w,
                members=tuple(sorted(self.members)),
                blunt=True,
            ),
        )

    def reserve_w(self) -> float:
        """Return the unmetered load behind the fuse: nobody grants it and it draws."""
        return self.unmetered_w

    def report_row(self, grants: Mapping[str, float]) -> tuple[float | None, float, bool]:
        """Return `(measured, reserved, breached)` for the report (D6 §4)."""
        measured = self._measured_total()
        reserved = self._reserved_total(grants)
        return measured, reserved, measured is not None and measured > self.limit_w

    def _measured_total(self) -> float | None:
        """Return what the circuit draws: the sub-meter, or Σ members, plus unmetered.

        `None` when neither is knowable - a circuit with no sub-meter and no member
        that measures itself cannot be breach-detected, and saying so is better than
        inventing a number (D6 §8). `unmetered_w` is what no meter sees, so it is
        added beside either source, exactly as `cap_w` subtracts it (D6 §5.8).
        """
        if self.sub_meter_w is not None:
            return self.sub_meter_w + self.unmetered_w
        if self._ctx is None:
            return None
        known = [
            watts
            for load_id in sorted(self.members)
            if (watts := measured_w(self._ctx.view(load_id))) is not None
        ]
        if not known:
            return None
        return sum(known) + self.unmetered_w

    def _reserved_total(self, grants: Mapping[str, float]) -> float:
        """Return what this circuit's members reserve under `grants`."""
        if self._ctx is None:
            return 0.0
        return self._ctx.reserved_of(
            {load_id: watts for load_id, watts in grants.items() if load_id in self.members}
        )
