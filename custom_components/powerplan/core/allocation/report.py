"""What one allocation tick decided, and the WARNING it logs (D6 §4, §5.9).

The report is the whole tick made inspectable: the grants, the reservation table,
the shed set with a reason per load, what each constraint bound, and the
`unconstrained_ask_w` diagnostic. D7 turns it into events and D8 into entities.

**The breach log exists because of one night on the ancestor controller.** On every tick
with a breach or a deficit, one WARNING line carries the reservation table - load,
granted, measured, nameplate, reserved - the stage and its reason, `P_allow`, `P_total`
and which constraint bound. The class of blind spot that let `p_free_w` read 8–9 kW
while the house was 1.4 kW over becomes visible the next time it happens, not a night
later. Rate-limited to one line per 60 s per condition.

`unconstrained_ask_w` is a **diagnostic** - "held back 3.4 kW this tick". The
accounting counterfactual is D11's and the word is reserved for it (D6 §1).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from .reserved import measured_w, reserved_w

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from ..metering import ControlledView
    from ..model import Grant
    from ..strategies import LoadView

__all__ = [
    "AllocReport",
    "BreachLog",
    "CircuitReport",
    "PhaseReport",
    "ReservedRow",
    "RotationReport",
    "ShedReason",
    "ZoneReport",
    "reservation_table",
    "unconstrained_ask_w",
]

_LOGGER = logging.getLogger(__name__)

#: One breach line per condition per this many seconds (D6 §5.9).
BREACH_LOG_INTERVAL_S = 60.0


class ShedReason(StrEnum):
    """Why a load is being held back (D6 §5.3 step 7).

    A closed vocabulary: a load with a zero grant is only *shed* if one of these
    says so, and a new constraint brings its own member rather than a conditional
    somewhere in the walk. `group_cap` and `zone_substituted` are declared here and
    used by WP3.2 and WP5.3.
    """

    BUDGET = "budget"
    STAGE = "stage"
    GROUP_CAP = "group_cap"
    ZONE_SUBSTITUTED = "zone_substituted"
    CIRCUIT = "circuit"
    PHASE = "phase"
    EXTERNAL_LIMIT = "external_limit"
    TRIM = "trim"
    #: The grid's relay has the load switched off (G14).
    GRID_SWITCHED = "grid_switched"


@dataclass(frozen=True, slots=True)
class ReservedRow:
    """One load in the reservation table (D6 §4, §5.9)."""

    load: str
    granted_w: float
    measured_w: float | None
    nameplate_w: float
    reserved_w: float


@dataclass(frozen=True, slots=True)
class CircuitReport:
    """What one circuit did this tick (D6 §4, §8)."""

    limit_w: float
    measured_w: float | None
    reserved_w: float
    members: tuple[str, ...]
    breach: bool
    #: Whether `measured_w` is the sub-meter's figure; `False` is the members' own
    #: sum plus the unmetered allowance - the fall-back when there is no sub-meter
    #: or it cannot answer this tick (D6 §8).
    sub_meter: bool = False


@dataclass(frozen=True, slots=True)
class PhaseReport:
    """The per-phase currents and what was left of them, in amps (D6 §4)."""

    limit_a: float
    amps: tuple[float, ...]
    headroom_a: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class RotationReport:
    """What a group's rotation decided (D6 §5.6, INV-41).

    `reason` is why it decided anything at all - `stage`, `projection`, or
    `no scarcity`, in which case the group made no decision and `chosen` is empty.
    `queue` is the ranking it admitted from: `(load, deficit against the comfort
    target, nameplate)`, coldest first, with a starved member at the front.
    """

    active: bool
    reason: str
    cap_w: float | None
    chosen: tuple[str, ...]
    demand_w: float
    queue: tuple[tuple[str, float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class ZoneReport:
    """Which source carries a zone's demand, and what it cost (D6 §5.7, INV-42).

    `cost_per_kwh_heat` is the whole decision in one row per source - price over
    efficiency, in major units per kWh of **heat** - and `excluded` says why a
    source was not even a candidate: `cop_below_floor`, `no_price`, `absent`. Both
    are floats and strings because this is a diagnostic; the money that is summed
    is D11's (`design/DECISIONS.md` D-0246).
    """

    chosen: tuple[str, ...]
    reason: str
    substituted: tuple[str, ...] = ()
    cost_per_kwh_heat: Mapping[str, float] = field(default_factory=dict)
    excluded: Mapping[str, str] = field(default_factory=dict)
    demand_w: float = 0.0


@dataclass(frozen=True, slots=True)
class AllocReport:
    """Everything one tick decided and why (D6 §4)."""

    p_free_w: float
    comfort: tuple[str, ...] = ()
    granted: tuple[str, ...] = ()
    denied: tuple[tuple[str, str], ...] = ()
    shed: tuple[str, ...] = ()
    shed_reason: Mapping[str, str] = field(default_factory=dict)
    trimmed: tuple[str, ...] = ()
    trim_freed_w: float = 0.0
    reserved: tuple[ReservedRow, ...] = ()
    rotation: Mapping[str, RotationReport] = field(default_factory=dict)
    zones: Mapping[str, ZoneReport] = field(default_factory=dict)
    circuits: Mapping[str, CircuitReport] = field(default_factory=dict)
    phases: PhaseReport | None = None
    #: How far the comfort grants went over the allowance - HLD §3's one named
    #: exception. A non-empty `comfort` beside a positive `breach_w` is what D7
    #: publishes as `comfort_over_allowance`.
    breach_w: float = 0.0
    #: Measured total less the allowance, floored at 0 (INV-38: instantaneous).
    deficit_w: float = 0.0
    #: The other side of the same number: how far under the allowance we are.
    headroom_w: float = 0.0
    blunt: bool = False
    frozen: bool = False
    ev_stop_ok: Mapping[str, bool] = field(default_factory=dict)
    unconstrained_ask_w: float = 0.0
    violations: tuple[str, ...] = ()

    @property
    def over_allowance(self) -> bool:
        """Whether this tick breached or ran a deficit - what the log is keyed on."""
        return self.breach_w > 0.0 or self.deficit_w > 0.0


def unconstrained_ask_w(loads: Iterable[LoadView]) -> float:
    """Return Σ what the loads that wanted power would have taken (D6 §5.3 step 10).

    Power is signed, so a battery offering discharge is not asking for anything: only
    positive asks are summed.
    """
    return sum(max(0.0, load.demand.max_w) for load in loads if load.demand.wants)


def reservation_table(
    loads: Iterable[LoadView],
    grants: Mapping[str, Grant],
    views: Mapping[str, ControlledView],
) -> tuple[ReservedRow, ...]:
    """Return the granted / measured / nameplate / reserved table (D6 §4, §5.9)."""
    rows: list[ReservedRow] = []
    for load in loads:
        grant = grants.get(load.load_id)
        granted = 0.0 if grant is None else grant.w
        view = views.get(load.load_id)
        rows.append(
            ReservedRow(
                load=load.load_id,
                granted_w=granted,
                measured_w=measured_w(view),
                nameplate_w=load.nameplate_w,
                reserved_w=reserved_w(load, granted, view),
            )
        )
    return tuple(rows)


class BreachLog:
    """The rate limiter behind D6 §5.9's WARNING line.

    Stateful on purpose and owned by the runtime: one line per condition per minute,
    so a breach that lasts an hour is visible without drowning the log.
    """

    def __init__(self, interval_s: float = BREACH_LOG_INTERVAL_S) -> None:
        """Build a log that speaks at most once per `interval_s` per condition."""
        self.interval_s = interval_s
        self._last: dict[str, datetime] = {}

    def log(
        self,
        report: AllocReport,
        *,
        stage: int,
        reason: str,
        p_allow_w: float,
        p_total_w: float | None,
        now: datetime,
    ) -> bool:
        """Log the breach line if this condition has not been logged lately.

        Returns whether it spoke, so a test and the engine can both tell.
        """
        if not report.over_allowance:
            return False
        condition = "breach" if report.breach_w > 0.0 else "deficit"
        last = self._last.get(condition)
        if last is not None and (now - last).total_seconds() < self.interval_s:
            return False
        self._last[condition] = now
        _LOGGER.warning(
            "%s: breach %.0f W, deficit %.0f W, stage %d (%s), P_allow %.0f W, "
            "P_total %s, bound by %s; reservations %s",
            condition,
            report.breach_w,
            report.deficit_w,
            stage,
            reason,
            p_allow_w,
            "unknown" if p_total_w is None else f"{p_total_w:.0f} W",
            ", ".join(report.violations) or "nothing",
            " | ".join(
                f"{row.load} granted {row.granted_w:.0f} measured "
                f"{'?' if row.measured_w is None else f'{row.measured_w:.0f}'} "
                f"nameplate {row.nameplate_w:.0f} reserved {row.reserved_w:.0f}"
                for row in report.reserved
            ),
        )
        return True
