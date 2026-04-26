"""A running programme keeps its power until it is finished (D6 §5.7, INV-59).

An interrupted dishwasher is a **restarted** dishwasher: the water is heated twice,
the energy is spent twice, and what the household finds is a machine full of dirty
water. So a cycle that has started is granted its profile power before the priority
walk - like a comfort violator - is out of the trim's candidate list, and survives
every stage below 4. Stage 4 is the exception, and only stage 4: a fuse, a
contracted trip, a spent window and a DSO event are not preferences.

The other half is that nothing is held on spec. A cycle that has been *requested*
but has not started reserves nothing and grants nothing: it is an ordinary plan cap
until the appliance reports that it is running (D6 §2, D4 §5.13).

`power_w` is the learned or default profile's power **now** (D4 §5.13), which is
what the reservation pins here. What the walk then charges the headroom follows
`reserved.py`'s rule for the load's control kind, so a relay-controlled appliance
holds at least its nameplate - D6 §2's figure, and the conservative direction
(`design/DECISIONS.md` D-0245).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..report import ShedReason

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ...strategies import LoadView
    from .base import AllocCtx, Scope, Violation

__all__ = ["CycleReservation"]


class CycleReservation:
    """One appliance cycle and whether it is running (D6 §2, §5.3 step 4)."""

    scope: ClassVar[Scope] = "load"
    shed_reason: ClassVar[ShedReason] = ShedReason.STAGE

    def __init__(
        self,
        *,
        load_id: str,
        power_w: float,
        running: bool = True,
        started_at: datetime | None = None,
        key: str | None = None,
    ) -> None:
        """Build the reservation; `power_w` is the profile's power now (D4 §5.13)."""
        self.load_id = load_id
        self.power_w = power_w
        self.running = running
        self.started_at = started_at
        self.key = key if key is not None else f"cycle_{load_id}"

    def prepare(self, ctx: AllocCtx) -> None:
        """Nothing to measure: whether the programme is running is D4's report."""

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return the programme's power for its own load while it runs, else `None`.

        A running cycle is not paced - it is owned by the appliance - so the cap is
        also the grant: there is nothing to be gained by offering it more, and
        offering it less does not slow it down.
        """
        if not self.running or load.load_id != self.load_id:
            return None
        return self.power_w

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return nothing: a reservation is not a limit and cannot be breached."""
        return ()

    def reserve_w(self) -> float:
        """Return what the programme pins: its power now, or nothing before it starts."""
        return self.power_w if self.running else 0.0
