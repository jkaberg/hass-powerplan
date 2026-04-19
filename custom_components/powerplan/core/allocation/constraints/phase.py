"""Per-phase limits, in amps (D6 §5.8, INV-60).

Per-phase power is ill-defined on an IT system with no neutral, and every per-phase
limit is an ampere rating anyway (D3 §2): the constraint reads D3's
`limit_a − I_phase` and converts with the **load's own** `w_per_amp`. A load that
does not know which phases it sits on is bounded by the tightest phase - conservative
and correct.

The phase current already includes the asking load's own draw, so it is given back
before the load is judged: the same rule as `P_free` at site level (D6 §5.3). A phase
fuse is a fuse, so a negative headroom is a blunt violation scoped to the loads on
that phase, and missing phase currents make the constraint inactive rather than
closed - blindness never opens a gate, and it never slams one either (D6 §8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ...metering import Phase, headroom_a
from ..report import ShedReason
from ..reserved import measured_w
from .base import Violation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ...metering import ElectricalProfile, PhaseReadings
    from ...strategies import LoadView
    from .base import AllocCtx, Scope

__all__ = ["PhaseLimit"]

_ORDER = (Phase.L1, Phase.L2, Phase.L3)


class PhaseLimit:
    """The site's per-phase ampere limits (D3 §4, D6 §5.8)."""

    key: ClassVar[str] = "phase"
    scope: ClassVar[Scope] = "phase"
    shed_reason: ClassVar[ShedReason] = ShedReason.PHASE

    def __init__(self, electrical: ElectricalProfile) -> None:
        """Build the constraint for a site's electrical profile (its `w_per_amp`)."""
        self.electrical = electrical
        self._readings: PhaseReadings | None = None
        self._ctx: AllocCtx | None = None

    def prepare(self, ctx: AllocCtx) -> None:
        """Take this tick's per-phase currents; `None` leaves the constraint inactive."""
        self._ctx = ctx
        self._readings = ctx.meter.phases

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return the load's phase headroom in watts, its own draw given back."""
        if self._readings is None or self._ctx is None:
            return None
        own = measured_w(self._ctx.view(load.load_id)) or 0.0
        amps = headroom_a(self._readings, load.phase_names)
        return max(0.0, amps * self.electrical.w_per_amp(load.phases) + own)

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return one blunt violation per over-current phase, scoped to its loads."""
        if self._readings is None or self._ctx is None:
            return ()
        out: list[Violation] = []
        for index, spare in enumerate(self._readings.headroom_a()):
            if spare >= 0.0 or index >= len(_ORDER):
                continue
            label = _ORDER[index]
            members = tuple(
                sorted(
                    load.load_id
                    for load in self._ctx.loads
                    if load.load_id in grants
                    and (load.phase_names is None or label.value in load.phase_names)
                )
            )
            out.append(
                Violation(
                    constraint=self.key,
                    scope_id=label.value,
                    excess_w=-spare * self.electrical.w_per_amp(self.electrical.service_phases()),
                    members=members,
                    blunt=True,
                )
            )
        return tuple(out)

    def reserve_w(self) -> float:
        """Return 0: a phase limit pins nothing, it only bounds."""
        return 0.0

    def readings(self) -> PhaseReadings | None:
        """Return this tick's readings, for the report (D6 §4)."""
        return self._readings
