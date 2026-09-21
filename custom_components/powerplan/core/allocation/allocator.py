"""The ordered walk: who gets what, and why (D6 §5.3, INV-1).

    0 frozen        → hold every previous grant, no escalation (INV-15, INV-17)
    0a discharge    → at stage ≥ 1 a battery above its reserve covers the measured deficit (Phase 7)
    1 zones         → which source carries a zone's demand              (INV-42)
    2 P_free        = P_allow − uncontrolled − Σ reserved(decided so far)
    3 comfort       → every violator first, at any priority, bounded by item 1 only
    4 cycles        → a running cycle keeps its profile power            (INV-59)
    5 the walk      → descending priority, on the residual, quantised down;
                      a load with a grid limit follows the measured surplus (Phase 7)
    6 stage actions → ≥ 2 stores to their floor, 4 (blunt) all off but comfort and pumps
    7 the shed set  → filtered to agree with the grants, a reason each   (INV-40)
    8 EV stop gates → two gates, two horizons                           (INV-39)
    9 the trim      → against the measured total, if there is a deficit (INV-37)
   10 the report    → grants, reservations, reasons, the unconstrained ask

**This is the only place the precedence lives** (INV-1): hard limits > capacity
ceiling > comfort floors > plan > preference. A strategy paces and never overrides
safety; a device profile never decides.

Two invariants the arithmetic carries rather than checks. A load is judged against
the allowance less what the loads **decided before it** reserve, so it is never asked
to fit beside its own reservation (D6 §9 21) - and what a load reserves is its
nameplate, its measured draw plus a margin, or its grant, by what it *is* (D6 §5.2).
Both come from one night on the ancestor controller: `granted_w 348.3, measured_w 2940.0`
with `p_free_w` reading 8–9 kW while the house was 1.4 kW over.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..model import Grant
from .budget import PiState
from .constraints.base import AllocCtx, Constraint, Violation
from .constraints.circuit import CircuitLimit
from .constraints.cycle import CycleReservation
from .constraints.group import GroupCap
from .constraints.phase import PhaseLimit
from .constraints.zone import Zone, ZoneChoice
from .ladder import STAGE_BLUNT, LadderState
from .report import (
    AllocReport,
    CircuitReport,
    PhaseReport,
    RotationReport,
    ShedReason,
    ZoneReport,
    reservation_table,
    unconstrained_ask_w,
)
from .reserved import GRANT_MARGIN_W, MODULATING_KINDS, ON_W, reserved_w
from .trim import TrimCfg, proportional_trim

if TYPE_CHECKING:
    from ..model import Plan
    from ..strategies import LoadView

__all__ = ["EV_MIN_STOP_S", "AllocCfg", "AllocState", "Grants", "allocate"]

#: Ten minutes of EV sulk: below this a stop costs more than it saves (INV-39).
EV_MIN_STOP_S = 600.0

#: D6 §5.3 (Phase 7): a surplus-only load starts after this long with enough
#: surplus, and stops after `SURPLUS_STOP_S` of importing - evcc's enable and
#: disable delays, defaults tuned on `pv_no_battery_ev_waits`, not measured.
SURPLUS_START_S = 60.0
SURPLUS_STOP_S = 300.0

#: The constraint scopes that bound a comfort violator: item 1 of the precedence
#: plus the physical limits inside it - a grid-switched load's open relay among
#: them (G14). Groups and zones are preferences and do not apply to a floor (D6
#: §5.3 step 3).
_HARD_SCOPES = frozenset({"site", "circuit", "phase", "switched"})

#: Absorbs the float round trip when deciding which constraint bound a grant.
_EPS_W = 1e-6

#: What the walk's stages are named in `AllocReport.denied`.
_SATISFIED = "satisfied"
_PLANNED_IDLE = "planned idle"
_WAITING_FOR_SUN = "waiting for surplus"
#: What `Grant.capped_by` names when the measured surplus bound a grant.
_SURPLUS = "surplus"


type Grants = Mapping[str, Grant]


@dataclass(frozen=True, slots=True)
class AllocCfg:
    """The allocator's knobs (D6 §6). None of them appear in the guided flow."""

    grant_margin_w: float = GRANT_MARGIN_W
    ev_min_stop_s: float = EV_MIN_STOP_S
    on_w: float = ON_W
    #: From which stage a store is pulled to its comfort floor (D6 §5.4).
    store_from_stage: int = 2
    #: The stage at which stickiness stops protecting a running load (§5.3 step 5).
    sticky_max_stage: int = 3
    trim: TrimCfg = field(default_factory=TrimCfg)
    surplus_start_s: float = SURPLUS_START_S
    surplus_stop_s: float = SURPLUS_STOP_S


@dataclass(frozen=True, slots=True)
class AllocState:
    """What the allocator remembers between ticks, and D7 persists (D6 §4, §7).

    `starved_since` is a group member's rotation clock (D6 §5.6) and `zone_choice`
    a zone's source, its dwell and the candidate trying to replace it (§5.7). Both
    are rebuilt from the constraints that are present, and left untouched on a tick
    that has none - a tick without groups is not a tick in which nobody is starving
    (`design/DECISIONS.md` D-0240).
    """

    sticky_until: Mapping[str, datetime] = field(default_factory=dict)
    starved_since: Mapping[str, datetime] = field(default_factory=dict)
    zone_choice: Mapping[str, ZoneChoice] = field(default_factory=dict)
    ev_stop_latch: Mapping[str, datetime | None] = field(default_factory=dict)
    pi: PiState = field(default_factory=PiState)
    ladder: LadderState = field(default_factory=LadderState)
    #: D6 §5.3 (Phase 7): since when a stopped surplus-only load has had enough
    #: surplus, and since when a running one has imported.
    sun_since: Mapping[str, datetime] = field(default_factory=dict)
    import_since: Mapping[str, datetime] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-able form D7 persists (D6 §7)."""
        return {
            "sticky_until": {k: v.isoformat() for k, v in self.sticky_until.items()},
            "starved_since": {k: v.isoformat() for k, v in self.starved_since.items()},
            "zone_choice": {k: choice.as_dict() for k, choice in self.zone_choice.items()},
            "ev_stop_latch": {
                k: None if v is None else v.isoformat() for k, v in self.ev_stop_latch.items()
            },
            "pi": self.pi.as_dict(),
            "ladder": self.ladder.as_dict(),
            "sun_since": {k: v.isoformat() for k, v in self.sun_since.items()},
            "import_since": {k: v.isoformat() for k, v in self.import_since.items()},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AllocState:
        """Return the state `as_dict()` wrote."""
        return cls(
            sticky_until={
                k: datetime.fromisoformat(v) for k, v in data.get("sticky_until", {}).items()
            },
            starved_since={
                k: datetime.fromisoformat(v) for k, v in data.get("starved_since", {}).items()
            },
            zone_choice={
                k: ZoneChoice.from_dict(choice) for k, choice in data.get("zone_choice", {}).items()
            },
            ev_stop_latch={
                k: None if v is None else datetime.fromisoformat(v)
                for k, v in data.get("ev_stop_latch", {}).items()
            },
            pi=PiState.from_dict(dict(data["pi"])) if "pi" in data else PiState(),
            ladder=(
                LadderState.from_dict(dict(data["ladder"])) if "ladder" in data else LadderState()
            ),
            sun_since={k: datetime.fromisoformat(v) for k, v in data.get("sun_since", {}).items()},
            import_since={
                k: datetime.fromisoformat(v) for k, v in data.get("import_since", {}).items()
            },
        )


def allocate(
    ctx: AllocCtx,
    constraints: Sequence[Constraint],
    cfg: AllocCfg,
    state: AllocState,
) -> tuple[Grants, AllocReport, AllocState]:
    """Decide every load's grant for this tick (D6 §3, §5.3).

    `ctx` carries the tick - the meter, the budget, the loads D5 planned, their
    plans, their meter rows and last tick's grants - so the constraints and the walk
    read the same frozen inputs (`design/DECISIONS.md` D-0162).
    """
    if ctx.frozen:
        return _frozen(ctx, state)

    for constraint in constraints:
        # The two constraints with memory take last tick's from the state before
        # they rank (D6 §7): rotation clocks, and a zone's source and dwell.
        if isinstance(constraint, GroupCap):
            constraint.seed(state.starved_since)
        elif isinstance(constraint, Zone):
            constraint.seed(state.zone_choice.get(constraint.key))
        constraint.prepare(ctx)

    order = sorted(ctx.loads, key=lambda load: (-load.priority, load.load_id))
    ceiling_w = ctx.budget.p_allow_w - (ctx.meter.uncontrolled_w or 0.0)
    walk = _Walk(ctx=ctx, cfg=cfg, constraints=constraints, state=state, ceiling_w=ceiling_w)

    walk.serve_discharge(order)
    walk.serve_comfort(order)
    walk.serve_cycles()
    walk.serve_rest(order)
    violations = walk.apply_violations()
    trimmed, freed = walk.trim()
    report = walk.report(violations=violations, trimmed=trimmed, freed=freed)
    return walk.grants, report, walk.new_state()


def _frozen(ctx: AllocCtx, state: AllocState) -> tuple[Grants, AllocReport, AllocState]:
    """Return the previous grants unchanged (D6 §5.3 step 0, INV-15, INV-17).

    At a window seam the ancestor controller's the allowance answered 0 W with under a
    second left - arithmetically right, and it shed the house into a window that had all
    its capacity free, for 76 seconds. "Hold still" is the right answer to blindness;
    "open up" is not, and neither is "shed everything". The shed state is held too, so
    holding still does not read as "nothing is shed".
    """
    grants: dict[str, Grant] = {}
    shed: list[str] = []
    reasons: dict[str, str] = {}
    for load in ctx.loads:
        previous = ctx.previous.get(load.load_id)
        grant = (
            previous
            if previous is not None
            else Grant(
                w=0.0,
                shed=False,
                shed_reason=None,
                stop_ok=False,
                stage=ctx.stage,
                blunt=ctx.blunt,
                capped_by=(),
            )
        )
        grants[load.load_id] = grant
        if grant.shed and grant.w <= 0.0:
            shed.append(load.load_id)
            reasons[load.load_id] = grant.shed_reason or ShedReason.STAGE
    held = sum(
        reserved_w(load, grants[load.load_id].w, ctx.view(load.load_id)) for load in ctx.loads
    )
    report = AllocReport(
        p_free_w=max(0.0, ctx.budget.p_allow_w - (ctx.meter.uncontrolled_w or 0.0) - held),
        granted=tuple(sorted(lid for lid, g in grants.items() if g.w > 0.0)),
        shed=tuple(sorted(shed)),
        shed_reason=reasons,
        reserved=reservation_table(ctx.loads, grants, ctx.views),
        blunt=ctx.blunt,
        frozen=True,
        unconstrained_ask_w=unconstrained_ask_w(ctx.loads),
    )
    return grants, report, state


@dataclass
class _Walk:
    """One tick's walk. Mutable by construction: it is the loop, not a value."""

    ctx: AllocCtx
    cfg: AllocCfg
    constraints: Sequence[Constraint]
    state: AllocState
    ceiling_w: float

    def __post_init__(self) -> None:
        """Start with nothing decided and nothing reserved."""
        self.grants: dict[str, Grant] = {}
        self.taken: dict[str, float] = {}
        self.reserved_running: float = 0.0
        self.comfort: list[str] = []
        self.cycles: list[str] = []
        self.denied: list[tuple[str, str]] = []
        self.stop_ok: dict[str, bool] = {}
        self.sticky: dict[str, datetime] = dict(self.state.sticky_until)
        self.breach_w: float = 0.0
        #: The measured surplus the loads that follow it have left (D6 §5.3,
        #: Phase 7); computed on first use, taken in the walk's priority order.
        self.sun_left: float | None = None
        self.sun_since: dict[str, datetime] = dict(self.state.sun_since)
        self.import_since: dict[str, datetime] = dict(self.state.import_since)

    # ------------------------------------------------------------------ stages #

    def serve_discharge(self, order: Sequence[LoadView]) -> None:
        """Discharge a battery into the measured deficit at stage ≥ 1, first (D6 §5.3, Phase 7).

        Faster than the planning cycle's `peak_shave`: a battery above its reserve
        (its demand offers discharge, `min_w < 0`) is granted
        `−min(deficit + what it already delivers, its inverter)` before any comfort
        is served or shed, and the room it frees is the walk's. At stage 0 the plan
        governs; below the reserve the demand offers no discharge at all.
        """
        grid = self.ctx.meter.grid_w
        if self.ctx.stage < 1 or grid is None:
            return
        deficit = grid - self.ctx.budget.p_allow_w
        for load in order:
            if deficit <= 0.0:
                return
            if load.demand.min_w >= 0.0 or load.load_id in self.grants:
                continue
            delivering = max(0.0, -self._drawn(load))
            watts = min(deficit + delivering, -load.demand.min_w)
            self._give(load, -watts, capped_by=(), reason="discharge")
            deficit -= watts - delivering

    def serve_comfort(self, order: Sequence[LoadView]) -> None:
        """Grant every comfort-floor violator, whatever its priority (HLD §3, INV-1).

        The one named exception to the ceiling: a violated floor is granted up to the
        **hard limits** and no further - a step costs about 200 NOK a month, a cold
        bathroom costs trust in the whole system, and only one of the two is
        recoverable. Over the allowance the controller serves the comfort, records the
        breach and logs it loudly (D6 §5.9).
        """
        for load in order:
            comfort = load.demand.comfort
            if comfort is None or not comfort.violated:
                continue
            cap, capped_by = self._cap(load, hard_only=True)
            watts = self._quantise(load, min(load.demand.max_w, cap), stop_ok=False)
            self._give(load, watts, capped_by=capped_by, reason="comfort")
            self.comfort.append(load.load_id)
        self.breach_w = max(0.0, self.reserved_running - self.ceiling_w)

    def serve_cycles(self) -> None:
        """Grant every running cycle its profile power, before the walk (INV-59).

        An interrupted dishwasher is a restarted dishwasher: the energy is spent
        twice and the load comes back out dirty. So a started programme is served
        like a comfort violator - at any priority, bounded by the **hard** limits
        only - and stage 4 is the one thing that takes it, because a fuse, a
        contracted trip, a spent window and a DSO event are not preferences.
        """
        for constraint in self.constraints:
            if not isinstance(constraint, CycleReservation) or not constraint.running:
                continue
            load = self.ctx.load(constraint.load_id)
            if load is None or load.load_id in self.grants:
                continue
            self.cycles.append(load.load_id)
            if self.ctx.stage >= STAGE_BLUNT and self.ctx.blunt:
                self._deny(load, ShedReason.STAGE, "stage 4: fuse, trip, spent or external")
                continue
            cap, capped_by = self._cap(load, hard_only=True)
            self._give(
                load, min(constraint.power_w, cap), capped_by=capped_by, reason="running cycle"
            )

    def serve_rest(self, order: Sequence[LoadView]) -> None:
        """Walk the remaining loads in descending priority (D6 §5.3 step 5)."""
        for load in order:
            if load.load_id in self.grants:
                continue
            self._decide(load)

    def apply_violations(self) -> tuple[Violation, ...]:
        """Apply every blunt violation to its own members and nothing else (INV-60).

        A circuit breach is a `fuse_breach` for the loads behind that fuse: the site
        keeps allocating, because 32 A of garage says nothing about the other 60 amps.
        The exemptions are stage 4's own - a comfort violator and a heat pump stay.
        """
        found: list[Violation] = []
        for constraint in self.constraints:
            found.extend(constraint.post({lid: g.w for lid, g in self.grants.items()}))
        for violation in found:
            if not violation.blunt:
                continue
            reason = _reason_of(self.constraints, violation.constraint)
            for load_id in violation.members:
                load = self.ctx.load(load_id)
                if load is None or load_id in self.comfort or not load.sheddable:
                    continue
                grant = self.grants[load_id]
                if grant.w <= 0.0:
                    # Nothing to take. A member the cap already denied keeps its
                    # reason and turns blunt, so its write is urgent; one that is
                    # satisfied at zero is not a shed at all (INV-25).
                    if grant.shed:
                        self.grants[load_id] = replace(grant, stage=STAGE_BLUNT, blunt=True)
                    continue
                self._restate(load, reason=reason, stage=STAGE_BLUNT, blunt=True)
        return tuple(found)

    def trim(self) -> tuple[tuple[str, ...], float]:
        """Take back the measured deficit, ascending priority (D6 §5.5, INV-37)."""
        total = self.ctx.meter.grid_w
        if total is None or total - self.ctx.budget.p_allow_w <= 0.0:
            return (), 0.0
        before = dict(self.grants)
        after, freed = proportional_trim(
            self.ctx.loads,
            before,
            total - self.ctx.budget.p_allow_w,
            frozenset(self.comfort) | frozenset(self.cycles),
            self.cfg.trim,
            views=self.ctx.views,
            blunt=self.ctx.blunt,
            stop_ok=self.stop_ok,
        )
        self.grants = dict(after)
        self._recount()
        return (
            tuple(sorted(lid for lid, grant in after.items() if grant.w != before[lid].w)),
            freed,
        )

    # ------------------------------------------------------------------ report #

    def report(
        self, *, violations: tuple[Violation, ...], trimmed: tuple[str, ...], freed: float
    ) -> AllocReport:
        """Build the tick's report (D6 §4, §5.9)."""
        total = self.ctx.meter.grid_w
        deficit = 0.0 if total is None else max(0.0, total - self.ctx.budget.p_allow_w)
        headroom = 0.0 if total is None else max(0.0, self.ctx.budget.p_allow_w - total)
        shed = tuple(sorted(lid for lid, grant in self.grants.items() if grant.shed))
        return AllocReport(
            p_free_w=max(0.0, self.ceiling_w - self.reserved_running),
            comfort=tuple(self.comfort),
            granted=tuple(sorted(lid for lid, grant in self.grants.items() if grant.w > 0.0)),
            denied=tuple(self.denied),
            shed=shed,
            shed_reason={lid: self.grants[lid].shed_reason or ShedReason.BUDGET for lid in shed},
            trimmed=trimmed,
            trim_freed_w=freed,
            reserved=reservation_table(self.ctx.loads, self.grants, self.ctx.views),
            rotation=self._rotation(),
            zones=self._zones(),
            circuits=self._circuits(),
            phases=self._phases(),
            breach_w=self.breach_w,
            deficit_w=deficit,
            headroom_w=headroom,
            blunt=self.ctx.blunt,
            frozen=False,
            ev_stop_ok=dict(self.stop_ok),
            unconstrained_ask_w=unconstrained_ask_w(self.ctx.loads),
            violations=tuple(sorted({v.constraint for v in violations})),
        )

    def new_state(self) -> AllocState:
        """Return the state for the next tick: the clocks, the choices and the gates."""
        return replace(
            self.state,
            sticky_until=self.sticky,
            starved_since=self._starvation(),
            zone_choice=self._zone_choices(),
            ev_stop_latch={lid: self.ctx.now for lid, ok in self.stop_ok.items() if ok},
            sun_since=self.sun_since,
            import_since=self.import_since,
        )

    # --------------------------------------------------------------- internals #

    def _decide(self, load: LoadView) -> None:
        """Decide one load in the priority walk (D6 §5.3 steps 5–8)."""
        demand = load.demand
        stop_ok, _plan_stop = self._gates(load)
        if load.kind in MODULATING_KINDS:
            self.stop_ok[load.load_id] = stop_ok

        if not demand.wants:
            # ZERO, BUT NOT SHED (INV-25): a floor at its target, a tank already hot.
            # Putting it in eco buys nothing and costs the next window's recovery.
            self._give(load, 0.0, capped_by=(), reason=_SATISFIED, shed=False)
            return

        plan = self.ctx.plans.get(load.load_id)
        plan_cap = None if plan is None else plan.cap_w(self.ctx.now)
        if plan_cap is not None and plan_cap < 0.0 and demand.min_w < 0.0:
            # A PLANNED DISCHARGE (D5 §5.8, Phase 7): the plan governs a battery at
            # stage 0, into what the house imports and never into export.
            self._give(
                load, -self._discharge_w(load, -plan_cap), capped_by=("plan",), reason="discharge"
            )
            return
        if plan_cap is not None and plan_cap <= 0.0:
            # PACING, NOT SHEDDING (INV-25, INV-30): the plan says "not in this slot",
            # which for a store means standing still at its resting setpoint.
            watts = self._quantise(load, 0.0, stop_ok=stop_ok, session=self._session(load))
            self._give(load, watts, capped_by=("plan",), reason=_PLANNED_IDLE, shed=False)
            return

        cap, capped_by = self._cap(load)
        if plan_cap is not None and plan_cap < cap:
            cap, capped_by = plan_cap, ("plan",)
        follow = self._follow(load, plan)
        if follow is not None and follow < cap:
            if follow <= _EPS_W:
                # WAITING, NOT SHED (INV-25): a surplus-only load with no sun yet.
                watts = self._quantise(load, 0.0, stop_ok=stop_ok, session=self._session(load))
                self._give(load, watts, capped_by=(_SURPLUS,), reason=_WAITING_FOR_SUN)
                return
            cap, capped_by = follow, (_SURPLUS,)
        want = min(demand.max_w, cap)

        if (
            cap <= _EPS_W
            and self._quantise(load, 0.0, stop_ok=stop_ok, session=self._session(load)) <= 0.0
        ):
            # A CONSTRAINT said zero - a group's cap, a zone's other source, a
            # circuit with nothing left behind its fuse. That is a shed, with that
            # constraint's own reason, and not a silent zero grant (INV-40). A
            # modulating load whose stop is vetoed falls through and is held at its
            # floor instead (INV-39, `design/DECISIONS.md` D-0247).
            self._deny(load, self._binding_reason(capped_by, cap), "capped to 0 W")
            return

        if not load.sheddable:
            self._serve_protected(load, want, capped_by)
            return

        self._admit(load, want=want, cap=cap, capped_by=capped_by, stop_ok=stop_ok)

    def _admit(  # noqa: PLR0911 - D6 §5.3 steps 5–6: one return per way a load is decided
        self,
        load: LoadView,
        *,
        want: float,
        cap: float,
        capped_by: tuple[str, ...],
        stop_ok: bool,
    ) -> None:
        """Grant, hold or shed a sheddable load on the residual (D6 §5.3 steps 5–6)."""
        demand = load.demand
        blunt_stage = self.ctx.stage >= STAGE_BLUNT and self.ctx.blunt
        if blunt_stage and not (load.kind in MODULATING_KINDS and not stop_ok):
            # A blunt stage 4 takes everything - except a charging session the
            # minimum-duration guard says is not worth ending, which falls through to
            # the residual below and is held at its floor (INV-39).
            self._deny(load, ShedReason.STAGE, "stage 4: fuse, trip, spent or external")
            return

        if (
            self.ctx.stage >= self.cfg.store_from_stage
            and demand.comfort is not None
            and load.kind not in MODULATING_KINDS
        ):
            self._deny(
                load, ShedReason.STAGE, f"stage {self.ctx.stage}: store to its comfort floor"
            )
            return

        room = self.ceiling_w - self.reserved_running
        if load.kind in MODULATING_KINDS:
            watts = self._quantise(
                load, min(want, room), stop_ok=stop_ok, session=self._session(load)
            )
            if watts <= 0.0:
                self._deny(load, self._binding_reason(capped_by, cap), "nothing left to modulate")
                return
            self._give(load, watts, capped_by=capped_by, reason="residual")
            return

        need = load.nameplate_w
        if cap + _EPS_W < need and "plan" not in capped_by:
            # A relay draws its nameplate or nothing (D6 §5.2): a constraint's cap
            # that does not cover it is a denial with that constraint's reason,
            # never a partial grant the relay would overdraw - a garage circuit
            # with 4.7 kW left cannot hold a 6 kW sauna (D-0284). A plan's
            # cap is pacing, which the kind turns into a duty cycle, and stays.
            self._deny(
                load,
                self._binding_reason(capped_by, 0.0),
                f"cap {cap:.0f} W below the {need:.0f} W nameplate",
            )
            return
        if room + _EPS_W >= need:
            self._give(load, want, capped_by=capped_by, reason="priority")
            return
        if self._is_sticky(load):
            self._give(load, want, capped_by=capped_by, reason="sticky")
            return
        self._deny(
            load,
            self._binding_reason(capped_by, cap),
            f"not enough free ({room:.0f} W < {need:.0f} W needed)",
        )

    def _serve_protected(self, load: LoadView, want: float, capped_by: tuple[str, ...]) -> None:
        """Grant a load nothing below a blunt stage 4 may take (D4 §5.4, D6 §5.5).

        A heat pump's grant is a **permission**, and it is its measured draw plus room
        to modulate up, capped at rated power: an inverter at 23 W does not reserve
        3 kW, and publishing the flat rated figure invites the conclusion that the
        pump is starved when a thermostatic load drawing little is saturated.
        """
        if self.ctx.stage >= STAGE_BLUNT and self.ctx.blunt:
            self._deny(load, ShedReason.STAGE, "stage 4: fuse, trip, spent or external")
            return
        reserved = reserved_w(load, want, self.ctx.view(load.load_id))
        self._give(load, min(want, reserved), capped_by=capped_by, reason="protected")

    def _give(
        self,
        load: LoadView,
        watts: float,
        *,
        capped_by: tuple[str, ...],
        reason: str,
        shed: bool = False,
    ) -> None:
        """Record a grant and charge the headroom what it reserves (D6 §5.2)."""
        grant = Grant(
            w=watts,
            shed=shed,
            shed_reason=None,
            stop_ok=self.stop_ok.get(load.load_id, False),
            stage=self.ctx.stage,
            blunt=self.ctx.blunt,
            capped_by=capped_by,
        )
        self.grants[load.load_id] = grant
        self.taken[load.load_id] = watts
        self.reserved_running += reserved_w(load, watts, self.ctx.view(load.load_id))
        if reason in {_SATISFIED, _PLANNED_IDLE}:
            self.denied.append((load.load_id, reason))
        self._touch_sticky(load, watts)

    def _discharge_w(self, load: LoadView, planned_w: float) -> float:
        """Return the watts a battery discharges for a planned `planned_w` (D6 §5.3, Phase 7).

        Bounded by its inverter (`-min_w`) and by what the house would import
        without it - the grid now plus what it already delivers - so a discharge
        planned against the import price displaces import and is never sold at
        the export price.
        """
        watts = min(planned_w, -load.demand.min_w)
        grid = self.ctx.meter.grid_w
        if grid is None:
            return watts
        return max(0.0, min(watts, grid - self._drawn(load)))

    def _grid_limit(self, load: LoadView, plan: Plan | None) -> float | None:
        """Return what `load` may import now, or `None` when it does not follow the sun (D6 §5.3).

        A plan slot built on surplus says it (`grid_w`), and so does a planned
        charge of a load whose demand limits its import; otherwise the demand's
        own `import_w` - a battery's 0, so a slot its plan leaves open charges from
        the sun alone (D5 §5.8's "surplus-only charging, or hold"). A modulating load follows
        watts; a relay can only be surplus-only (`grid_w = 0`) - with a grid share
        it keeps its plan, as a setpoint or a mode does.
        """
        slot = None if plan is None else plan.slot_at(self.ctx.now)
        planned = slot is not None and slot.envelope_w is not None and slot.envelope_w > 0.0
        if (
            slot is not None
            and slot.grid_w is not None
            and planned
            and (slot.surplus_w > 0.0 or load.demand.import_w is not None)
        ):
            # The plan's own grid share: a sunny slot's, or a battery's planned charge.
            grid: float | None = slot.grid_w
        else:
            grid = load.demand.import_w
        if grid is None:
            return None
        if load.kind in MODULATING_KINDS or grid <= 0.0:
            return grid
        return None

    def _drawn(self, load: LoadView) -> float:
        """Return what `load` draws now, signed: measured, else what it was granted."""
        view = self.ctx.view(load.load_id)
        if view is not None and view.measured_w is not None:
            return view.measured_w
        return self.ctx.previous_w(load.load_id)

    def _sun(self) -> float:
        """Return the measured surplus left for the loads that follow it (D6 §5.3).

        What the site exports plus what those loads draw now - their draw came
        out of the surplus they are following, so it counts as theirs to take
        again. Taken in the walk's priority order.
        """
        if self.sun_left is None:
            grid = self.ctx.meter.grid_w
            drawn = sum(
                self._drawn(load)
                for load in self.ctx.loads
                if self._grid_limit(load, self.ctx.plans.get(load.load_id)) is not None
            )
            self.sun_left = 0.0 if grid is None else max(0.0, drawn - grid)
        return self.sun_left

    def _follow(self, load: LoadView, plan: Plan | None) -> float | None:
        """Return the most `load` may draw on the measured surplus, or `None` (D6 §5.3).

        `grid_w + its share`: it never imports more than its plan's grid share, so
        the capacity axis, which counts import only, sees nothing new (INV-19).
        A surplus-only load (`grid_w = 0`) has the start and stop delays.
        """
        grid = self._grid_limit(load, plan)
        if grid is None:
            return None
        sun = self._sun()
        allowed = grid + sun if grid > 0.0 else self._surplus_only(load, sun)
        self.sun_left = max(0.0, sun - max(0.0, min(allowed, load.demand.max_w) - grid))
        return allowed

    def _surplus_only(self, load: LoadView, sun: float) -> float:
        """Return a surplus-only load's allowance: start after 60 s of sun, stop after 300 s of import."""
        load_id = load.load_id
        now = self.ctx.now
        floor = self._sun_floor(load)
        if self._session(load):
            self.sun_since.pop(load_id, None)
            if sun >= floor:
                self.import_since.pop(load_id, None)
                return sun
            since = self.import_since.setdefault(load_id, now)
            if (now - since).total_seconds() >= self.cfg.surplus_stop_s:
                self.import_since.pop(load_id, None)
                return 0.0
            return max(sun, floor)
        self.import_since.pop(load_id, None)
        if sun < floor or sun <= 0.0:
            self.sun_since.pop(load_id, None)
            return 0.0
        since = self.sun_since.setdefault(load_id, now)
        return sun if (now - since).total_seconds() >= self.cfg.surplus_start_s else 0.0

    def _sun_floor(self, load: LoadView) -> float:
        """Return the surplus a surplus-only load needs to run: `min_surplus_w`, else its floor."""
        configured = float(load.params.get("min_surplus_w", 0) or 0)
        if configured > 0.0:
            return configured
        if load.kind in MODULATING_KINDS:
            return max(0.0, load.min_w)
        return load.nameplate_w

    def _deny(self, load: LoadView, why: ShedReason, detail: str) -> None:
        """Record a shed: a zero grant **because we are holding it back** (INV-40)."""
        self.grants[load.load_id] = Grant(
            w=0.0,
            shed=True,
            shed_reason=why,
            stop_ok=self.stop_ok.get(load.load_id, False),
            stage=self.ctx.stage,
            blunt=self.ctx.blunt,
            capped_by=(),
        )
        self.taken[load.load_id] = 0.0
        self.reserved_running += reserved_w(load, 0.0, self.ctx.view(load.load_id))
        self.denied.append((load.load_id, detail))
        self.sticky.pop(load.load_id, None)

    def _restate(self, load: LoadView, *, reason: ShedReason, stage: int, blunt: bool) -> None:
        """Re-decide one load at a scoped stage 4 (INV-60), keeping a vetoed EV afloat.

        A scoped breach is as blunt as the site's: a charger behind the breached
        fuse may stop on the same terms as under a site fuse breach - the window
        horizon's minimum-stop guard, not the site's own blunt flag, which a
        circuit breach leaves at 0 (D-0284).
        """
        grant = self.grants[load.load_id]
        if load.kind in MODULATING_KINDS:
            stop_ok = self.stop_ok.get(load.load_id, False) or self._budget_stop_ok(load)
            self.stop_ok[load.load_id] = stop_ok
            if not stop_ok:
                watts = self._quantise(load, 0.0, stop_ok=False, session=self._session(load))
                self.grants[load.load_id] = replace(grant, w=watts, stage=stage, blunt=blunt)
                self._recount()
                return
            grant = replace(grant, stop_ok=True)
        self.grants[load.load_id] = replace(
            grant, w=0.0, shed=True, shed_reason=reason, stage=stage, blunt=blunt
        )
        self._recount()

    def _recount(self) -> None:
        """Recompute what the decided grants reserve, after a rewrite."""
        self.reserved_running = sum(
            reserved_w(load, self.grants[load.load_id].w, self.ctx.view(load.load_id))
            for load in self.ctx.loads
            if load.load_id in self.grants
        )

    def _cap(self, load: LoadView, *, hard_only: bool = False) -> tuple[float, tuple[str, ...]]:
        """Return `(cap, keys that bound it)` over the constraints (D6 §2, INV-60)."""
        cap = math.inf
        limits: list[tuple[str, float]] = []
        for constraint in self.constraints:
            if hard_only and constraint.scope not in _HARD_SCOPES:
                continue
            limit = constraint.cap_w(load, self.taken)
            if limit is None:
                continue
            limits.append((constraint.key, limit))
            cap = min(cap, limit)
        return cap, tuple(key for key, limit in limits if limit <= cap + _EPS_W)

    def _binding_reason(self, capped_by: tuple[str, ...], cap: float) -> ShedReason:
        """Return what a zero grant is shed *for*: the constraint that bound it."""
        if cap <= _EPS_W:
            for constraint in self.constraints:
                if constraint.key in capped_by:
                    return constraint.shed_reason
        return ShedReason.BUDGET

    def _quantise(
        self, load: LoadView, watts: float, *, stop_ok: bool, session: bool = False
    ) -> float:
        """Return what the device will draw if granted `watts` (D6 §5.3 step 5).

        Down through the load's own `ControlKind`, so a vetoed stop returns the floor's
        watts and the headroom is charged the floor (INV-28, INV-39, D6 §9 23).
        """
        return load.quantise(max(0.0, watts), stop_ok=stop_ok, session_active=session)

    def _session(self, load: LoadView) -> bool:
        """Whether this load is running now: a stopped charger stays stopped (§5.3)."""
        if self.ctx.previous_w(load.load_id) > 0.0:
            return True
        view = self.ctx.view(load.load_id)
        return view is not None and (view.measured_w or 0.0) > self.cfg.on_w

    def _gates(self, load: LoadView) -> tuple[bool, bool]:
        """Return `(stop_ok, plan_stop)` for one load (D6 §5.3 step 8, INV-39).

            stop_ok = blunt ∧ min_stop_ok   OR   plan_stop ∧ plan_stop_ok

        A **budget** stop is judged on the window horizon, bounded by the plan's next
        active slot - it is undone by the window turning *or* by the plan. A **plan**
        stop is judged on the plan alone: using the window horizon for it is what
        enabled the charger at HH:50 and disabled it at HH:00, five hours running on the
        ancestor controller. Stage 3 never stops an EV; the trim holds it at its floor.
        """
        plan = self.ctx.plans.get(load.load_id)
        cap = None if plan is None else plan.cap_w(self.ctx.now)
        plan_stop = cap is not None and cap <= 0.0
        budget_ok = self._budget_stop_ok(load)
        # A plan that never draws again while the load still owes energy has run
        # out, not decided to idle: it was cut for a requirement that is still
        # there, and the next cycle re-cuts it. A pause between two blocks is a
        # decision; the floor until the re-cut is not a stop
        # (`design/DECISIONS.md` D-0253).
        owed = (load.demand.required_kwh or 0.0) > 0.0
        plan_ok = (
            plan is not None
            and plan.idle_seconds_from(self.ctx.now) >= self.cfg.ev_min_stop_s
            and (plan.next_active(self.ctx.now) is not None or not owed)
        )
        return (self.ctx.blunt and budget_ok) or (plan_stop and plan_ok), plan_stop

    def _budget_stop_ok(self, load: LoadView) -> bool:
        """Whether a blunt stop is worth it: the window horizon, bounded by the plan's next slot."""
        plan = self.ctx.plans.get(load.load_id)
        horizon_s = self.ctx.meter.t_rem_h * 3600.0
        if plan is not None:
            next_active = plan.next_active(self.ctx.now)
            if next_active is not None:
                horizon_s = min(horizon_s, (next_active - self.ctx.now).total_seconds())
        return horizon_s >= self.cfg.ev_min_stop_s

    def _is_sticky(self, load: LoadView) -> bool:
        """Whether a running load keeps its grant for `min_on_s` (D6 §5.3 step 5).

        Short cycling costs compressors and relays their life; stage 3 and above
        cancels it, because at that point it is serious.
        """
        if self.ctx.stage >= self.cfg.sticky_max_stage or self.ctx.previous_w(load.load_id) <= 0.0:
            return False
        until = self.sticky.get(load.load_id)
        return until is not None and self.ctx.now < until

    def _touch_sticky(self, load: LoadView, watts: float) -> None:
        """Start or keep this load's minimum-on clock (D6 §4 `sticky_until`)."""
        if watts <= 0.0 or load.min_on_s <= 0.0:
            self.sticky.pop(load.load_id, None)
            return
        if self.ctx.previous_w(load.load_id) <= 0.0 or load.load_id not in self.sticky:
            self.sticky[load.load_id] = self.ctx.now + timedelta(seconds=load.min_on_s)

    def _rotation(self) -> dict[str, RotationReport]:
        """Return one row per group constraint (D6 §4, §5.6)."""
        return {
            constraint.key: constraint.report()
            for constraint in self.constraints
            if isinstance(constraint, GroupCap)
        }

    def _zones(self) -> dict[str, ZoneReport]:
        """Return one row per zone constraint (D6 §4, §5.7)."""
        return {
            constraint.key: constraint.report()
            for constraint in self.constraints
            if isinstance(constraint, Zone)
        }

    def _starvation(self) -> Mapping[str, datetime]:
        """Return the rotation clocks the groups present keep (D6 §5.6).

        Rebuilt from those groups, so a member that has had its turn stops being
        owed one; a tick with no group constraint leaves the clocks alone rather
        than forgetting who was waiting (`design/DECISIONS.md` D-0240).
        """
        groups = [c for c in self.constraints if isinstance(c, GroupCap)]
        if not groups:
            return self.state.starved_since
        clocks: dict[str, datetime] = {}
        for group in groups:
            clocks.update(group.starvation())
        return clocks

    def _zone_choices(self) -> Mapping[str, ZoneChoice]:
        """Return each zone's source, dwell and candidate for the next tick (§5.7)."""
        zones = [c for c in self.constraints if isinstance(c, Zone)]
        if not zones:
            return self.state.zone_choice
        return {zone.key: choice for zone in zones if (choice := zone.choice()) is not None}

    def _circuits(self) -> dict[str, CircuitReport]:
        """Return one row per circuit constraint (D6 §4, §8)."""
        rows: dict[str, CircuitReport] = {}
        grants = {lid: grant.w for lid, grant in self.grants.items()}
        for constraint in self.constraints:
            if not isinstance(constraint, CircuitLimit):
                continue
            measured, reserved, breach = constraint.report_row(grants)
            rows[constraint.key] = CircuitReport(
                limit_w=constraint.limit_w,
                measured_w=measured,
                reserved_w=reserved,
                members=tuple(sorted(constraint.members)),
                breach=breach,
                sub_meter=constraint.sub_metered,
            )
        return rows

    def _phases(self) -> PhaseReport | None:
        """Return the per-phase row, or `None` when nothing measures the phases."""
        for constraint in self.constraints:
            if not isinstance(constraint, PhaseLimit):
                continue
            readings = constraint.readings()
            if readings is None:
                return None
            return PhaseReport(
                limit_a=readings.limit_a,
                amps=readings.amps,
                headroom_a=readings.headroom_a(),
            )
        return None


def _reason_of(constraints: Sequence[Constraint], key: str) -> ShedReason:
    """Return the shed reason of the constraint named `key`."""
    for constraint in constraints:
        if constraint.key == key:
            return constraint.shed_reason
    return ShedReason.BUDGET
