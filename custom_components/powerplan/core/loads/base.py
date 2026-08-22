"""The `Load` - a device the allocator can reason about (D4 §4.1, §5.1, §5.2).

A `Load` is `DeviceType` logic + `ControlKind` + `StoreModel` + `TargetProfile` +
a `WriteGate`, and it is **pure**: `observe()` reads, `apply()` decides, and the
write leaves as a `Command` on the `ApplyResult` for the executor to perform
(INV-2, INV-20). Nothing here touches Home Assistant, and nothing here decides a
grant - that is D6's job, and the precedence lives there and nowhere else
(INV-1).

The tick contract of D4 §5.1, with the state threaded so the whole thing stays a
function of its inputs (`design/DECISIONS.md` D-0063):

    load.observe(state, ctx)            → (LoadState, Observation)
    load.apply(grant, state, ctx)       → (LoadState, ApplyResult)
    load.release(state, ctx, reason)    → (LoadState, ApplyResult)   INV-26
    load.restore(state, ctx, reason)    → (LoadState, ApplyResult)   INV-29
    load.view_for_meter(state, ctx)     → ControlledView             D3 §5.8

`Role`, `Reads`, `Action`, `Command` and `Hold` are declared in `kinds/base.py`
and re-exported here, where D4 §4.1 says they live (`design/DECISIONS.md` D-0061).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, tzinfo
from enum import StrEnum
from math import copysign
from typing import Any, Literal, Protocol

from ..metering import ControlledView, ElectricalProfile
from ..model import Carrier, ComfortState, Demand, Grant, Mode, Quality, Urgency
from .gate import (
    TRANSIENT_GRACE_S,
    Action,
    Command,
    Decision,
    GateConfig,
    GateState,
    Transport,
    TransportBudget,
    config_for,
    decide,
)
from .kinds.base import (
    ActionReason,
    ControlKind,
    Desired,
    Hold,
    KindCtx,
    Reads,
    ReasonParams,
    Role,
    RoleRead,
    Value,
    Write,
)
from .stores.base import StoreCtx, StoreModel
from .targets import CalendarEvent, PresenceMode, TargetProfile

__all__ = [
    "Action",
    "ActionReason",
    "ApplyResult",
    "ComfortState",
    "Command",
    "ControlKind",
    "CyclePhase",
    "CycleProfile",
    "CycleState",
    "Desired",
    "GateConfig",
    "GateState",
    "Health",
    "Hold",
    "KindCtx",
    "Learned",
    "Load",
    "LoadConfig",
    "LoadCtx",
    "LoadState",
    "Mode",
    "Observation",
    "Reads",
    "ReasonParams",
    "Role",
    "RoleRead",
    "SessionDone",
    "Transition",
    "Transport",
    "TransportBudget",
    "TypeLogic",
    "Urgency",
    "Value",
    "Write",
    "as_on",
    "effective_mode",
    "expire_force",
    "recall",
    "remember",
    "transition",
]

#: The phase counts a load may be connected on (D3 §5.1). A subentry holds a
#: plain integer; this is what narrows it back to the type the profile takes.
_PHASE_COUNTS: Mapping[int, Literal[1, 2, 3]] = {1: 1, 2: 2, 3: 3}


# --------------------------------------------------------------------------- #
# Latches and learned values
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SessionDone:
    """The EV's session-done latch (D4 §5.11).

    It remembers the target and the SoC **at latch time**, because that is what
    makes each of the four clearing conditions fire once: a car that stopped at
    its own 80 % under a 100 % target is not re-armed every tick, and a top-up
    re-latches at the new level instead of oscillating around the old one.
    """

    at: datetime
    target_soc: float | None
    soc: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class Learned:
    """A value measurement replaced, with when and from how many samples (D4 §2).

    Bounded learning is D10's (INV-63); this is where a learned value is kept.

    It is also the types' **numeric memory between ticks**, which is the same
    shape - a number and when it was taken (`design/DECISIONS.md` D-0200): the
    sensorless tank's estimate (`tank_c`), the legionella hold accumulator
    (`legionella_hold_s`) and the previous outlet/power sample the heat pump's
    defrost detector compares against (`outlet_c`, `power_w`). Each owning type
    documents its keys.
    """

    value: float
    at: datetime
    samples: int = 1


class CyclePhase(StrEnum):
    """Where an appliance cycle stands (D4 §5.13).

    `planned` carries a start instant the plan chose, `started` is the write
    having gone out before the appliance has confirmed anything, and `running`
    is the appliance saying so itself. The three are deliberately distinct: a
    start that was written and never took is what the 2 × duration abort timeout
    catches, and INV-59 protects `started` and `running` alike.
    """

    IDLE = "idle"
    PLANNED = "planned"
    STARTED = "started"
    RUNNING = "running"
    FINISHED = "finished"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class CycleProfile:
    """What one run of an appliance costs and how long it takes (D4 §2, §6.8).

    `shape` is the ten-segment normalised power trace (each segment's share of
    the run's energy, summing to 1) that D6 reserves against; it is empty until
    a run has been measured. `learned` says whether this came off a real run or
    out of §6.8's table, because a learned value is bounded against the default
    and a default is never bounded against itself.
    """

    duration_s: float
    energy_kwh: float
    shape: tuple[float, ...] = ()
    learned: bool = False
    samples: int = 0

    @property
    def mean_w(self) -> float:
        """Average draw over the whole run - the flat reservation, if nothing else."""
        return 0.0 if self.duration_s <= 0.0 else self.energy_kwh * 3_600_000.0 / self.duration_s


@dataclass(frozen=True, slots=True)
class CycleState:
    """One appliance cycle's progress, and what it learned (D4 §4.1, §5.13).

    Persisted with the load: a cycle that survives a restart is the whole point
    of INV-59, and a run whose start powerplan has forgotten would be started
    twice. `segments` accumulates kWh into ten buckets as the run proceeds, so
    the learned `shape` costs ten floats rather than one sample per tick.
    """

    phase: CyclePhase = CyclePhase.IDLE
    requested_at: datetime | None = None
    start_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    energy_kwh: float = 0.0
    segments: tuple[float, ...] = ()
    #: When the machine last drew nothing, for the appliances that report no
    #: programme state at all: residual-heat drying looks exactly like finished.
    idle_since: datetime | None = None
    #: When the energy accumulator last took a sample, so the integral is over
    #: real time and not over a tick count.
    sampled_at: datetime | None = None
    profile: CycleProfile | None = None
    runs: int = 0

    @property
    def active(self) -> bool:
        """Whether a run is under way - the half of INV-59 a load can answer."""
        return self.phase in {CyclePhase.STARTED, CyclePhase.RUNNING}

    @property
    def notify_state(self) -> str:
        """D8 §5.6's `powerplan_cycle` state name for this phase, or `""` for none.

        `CyclePhase.PLANNED` is never assigned by `latch()` (`design/DECISIONS.md`
        D-0304): a request sits at `phase = IDLE` with `requested_at` set until
        the appliance starts, so "planned" reads that combination instead - "a
        cycle has been requested and has not started" is what the name means to
        a household, whichever phase value holds it. `started` covers `STARTED`
        and `RUNNING` alike: the household is told once that the machine is
        going, not a second time when it confirms.
        """
        if self.phase is CyclePhase.FINISHED:
            return "finished"
        if self.phase is CyclePhase.ABORTED:
            return "aborted"
        if self.phase in {CyclePhase.STARTED, CyclePhase.RUNNING}:
            return "started"
        if self.phase in {CyclePhase.IDLE, CyclePhase.PLANNED} and self.requested_at is not None:
            return "planned"
        return ""


# --------------------------------------------------------------------------- #
# Configuration and state
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """One load as its subentry describes it (D4 §4.1, §6).

    `params` is the **materialised** derivation (INV-66) - JSON scalars, exactly
    as `questionnaire.materialise()` wrote them. The type reads its own keys out
    of it; nothing else does.
    """

    load_id: str
    name: str
    type_key: str
    priority: int
    nameplate_w: float
    phases: Literal[1, 2, 3] = 1
    params: Mapping[str, Any] = field(default_factory=dict)
    target: TargetProfile | None = None
    transport: Transport = Transport.LOCAL
    carrier: Carrier = Carrier.ELECTRICITY
    efficiency: float = 1.0
    group: str | None = None
    substitutable: bool = True
    strategy: str = ""
    strategy_params: Mapping[str, Any] = field(default_factory=dict)
    phase_names: frozenset[str] | None = None
    command_min_interval_s: float = 0.0

    @classmethod
    def from_materialised(
        cls,
        data: Mapping[str, Any],
        *,
        load_id: str,
        name: str,
        target: TargetProfile | None = None,
        transport: Transport = Transport.LOCAL,
        phases: Literal[1, 2, 3] = 1,
        phase_names: frozenset[str] | None = None,
    ) -> LoadConfig:
        """Build a config from what the subentry holds (INV-66).

        The numbers come from the subentry, never from today's derivation table:
        that is the whole point of materialising them.
        """
        params = dict(data.get("params", {}))
        phase_count = _PHASE_COUNTS.get(int(params.get("phases", phases)), 1)
        return cls(
            load_id=load_id,
            name=name,
            type_key=str(data["type"]),
            priority=int(data.get("priority", 50)),
            nameplate_w=float(params.get("nameplate_w", 0.0)),
            phases=phase_count,
            params=params,
            target=target,
            transport=transport,
            group=data.get("group"),
            substitutable=bool(params.get("substitutable", True)),
            strategy=str(data.get("strategy", "")),
            strategy_params=dict(data.get("strategy_params", {})),
            phase_names=phase_names,
            command_min_interval_s=float(params.get("command_interval_s", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class LoadState:
    """What one load remembers between ticks, and persists (D4 §4.1, §7).

    One latch per type that has one: `session_done` is the EV's (D4 §5.11),
    `legionella_*` the tank's (§5.12, INV-54), `cycle` the appliance's (§5.13,
    INV-59) and `defrost_since` the heat pump's (§5.14, INV-29 - D4 §4.1
    amended, `design/DECISIONS.md` D-0200). `schema` is what migrates them in.
    """

    schema: int = 1
    mode: Mode = Mode.AUTO
    force_since: datetime | None = None
    force_max_h: float = 6.0
    shed_active: bool = False
    shed_since: datetime | None = None
    session_done: SessionDone | None = None
    legionella_last_completed: datetime | None = None
    legionella_in_progress_since: datetime | None = None
    cycle: CycleState | None = None
    defrost_since: datetime | None = None
    provisioned: Mapping[str, bool] = field(default_factory=dict)
    learned: Mapping[str, Learned] = field(default_factory=dict)
    last_target_restore_at: datetime | None = None
    commanded_w: float | None = None
    #: A charger granted and enabled that draws nothing (`ev`, D4 §5.11): since
    #: when, and the reason its `blocked_by` sensor gave once it counted.
    blocked_since: datetime | None = None
    blocked_reason: str | None = None
    #: Since when a bound role has been unreadable: what `Health.transient_since`
    #: carries while the load is transient for a stale role rather than a
    #: write (H.1 F-6, `design/DECISIONS.md` D-0362).
    stale_since: datetime | None = None
    #: The record `release()` and `restore()` undo: for every role powerplan has
    #: written since it last let go, what the device held before that first
    #: write (INV-26, INV-27, D-0360). Empty is a device
    #: powerplan has nothing outstanding on; `None` is a value it could not read.
    prior: Mapping[str, Value | None] = field(default_factory=dict)
    gate: GateState = field(default_factory=GateState)

    def __post_init__(self) -> None:
        """Require every force to carry a maximum duration (INV-57)."""
        if self.force_max_h <= 0.0:
            raise ValueError(
                "force_max_h must be positive: a force always carries a maximum "
                "duration and clears itself (INV-57)"
            )


@dataclass(frozen=True, slots=True)
class Health:
    """How well a load's bindings are answering (D4 §4.1, §8)."""

    ok: bool
    unhealthy: bool
    failures: int
    transient_since: datetime | None
    stale_roles: tuple[str, ...]
    last_error: str | None


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What one `apply()` did, and what the executor must now do (D4 §4.1).

    `command` is the write - `None` unless `action` is `written`. `budget` is the
    site's transport buckets after this load's decision: it threads from load to
    load in priority order, which is what makes INV-58 site-level.
    """

    action: Action
    value: Value | None
    reason: str
    command: Command | None = None
    blocking: bool = True
    verify_at: datetime | None = None
    effective_w: float | None = None
    budget: TransportBudget = field(default_factory=TransportBudget.empty)
    #: What the device held when the gate decided - the "old" of the executor's
    #: "old → new" line, an observed decision's included (H.1 F-4, D-0363).
    current: Value | None = None
    #: `reason` as a key and its numbers, for the surface to translate (D-0480).
    reason_key: ActionReason = field(kw_only=True)
    reason_params: ReasonParams = field(default_factory=dict, kw_only=True)

    @property
    def written(self) -> bool:
        """Whether something is to be sent to the device."""
        return self.action is Action.WRITTEN


@dataclass(frozen=True, slots=True)
class Observation:
    """What one `observe()` saw (D4 §5.1).

    The comfort state rides on the `Demand` (`Demand.comfort`), where D5 and D6
    read it; it is not repeated here.
    """

    demand: Demand
    measured_w: float | None
    health: Health
    #: What only a type with a plug knows (`ev`): a car on the cable, and its charge.
    connected: bool | None = None
    soc: float | None = None
    #: What only a type with a periodic hygiene cycle knows (`water_heater`, §5.12,
    #: INV-54): `None` for a type with no such cycle, or one running its own
    #: programme. `legionella_active` is the lead window or the cycle itself;
    #: `legionella_in_progress` the cycle alone; both feed D8's `powerplan_legionella`
    #: edges (`due`, `started`) and `legionella_due_at` its sensor.
    legionella_due_at: datetime | None = None
    legionella_last_completed: datetime | None = None
    legionella_active: bool | None = None
    legionella_in_progress: bool | None = None
    legionella_at_risk: bool | None = None
    #: What only a type with a run-once programme knows (`appliance_cycle`,
    #: §5.13, INV-59): `cycle_state` is `CycleState.notify_state` - `""` for a
    #: type with `LoadState.cycle` but nothing notify-worthy this tick, `None`
    #: for a type with no cycle at all - and `cycle_started_at` the run's own
    #: start once it has one. Both feed D8's `powerplan_cycle` edges.
    cycle_state: str | None = None
    cycle_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LoadCtx:
    """Everything outside a load that one tick of it depends on (D4 §5.1)."""

    now: datetime
    reads: Reads
    electrical: ElectricalProfile
    budget: TransportBudget = field(default_factory=TransportBudget.empty)
    site_active: bool = True
    presence: PresenceMode = PresenceMode.HOME
    setpoint_delta: float = 0.0
    desired: Desired | None = None
    calendar: tuple[CalendarEvent, ...] = ()
    outdoor_c: float | None = None
    indoor_c: float | None = None
    #: The site's local zone, for the things that are local statements: a
    #: departure table, a weekly schedule (HLD §7.1). `None` falls back to
    #: `now`'s own zone.
    zone: tzinfo | None = None

    def store_ctx(self) -> StoreCtx:
        """Return the context a store model needs (D4 §4.3)."""
        return StoreCtx(now=self.now, outdoor_c=self.outdoor_c, indoor_c=self.indoor_c)


# --------------------------------------------------------------------------- #
# Modes and transitions (D4 §5.2)
# --------------------------------------------------------------------------- #


def effective_mode(mode: Mode, site_active: bool) -> Mode:
    """Fold the site switch into the load's own mode (PLAN §7 dec. 20).

    `off` if the load is off; else `observe` if the site is inactive or the load
    is observing; else the load's own mode. The site switch being off does not
    change a load's configured mode - it releases every load on the edge
    (INV-26), then computes and publishes every decision (INV-44) while writing
    nothing, which is also what accrues D11's calibration slots.
    """
    if mode is Mode.OFF:
        return Mode.OFF
    if not site_active or mode is Mode.OBSERVE:
        return Mode.OBSERVE
    return mode


def expire_force(state: LoadState, now: datetime) -> LoadState:
    """Clear a `force` that has run its maximum duration (INV-57).

    The pyscript's expiry automation becomes a property of the mode: nothing
    outside has to remember to switch it back.
    """
    if state.mode is not Mode.FORCE or state.force_since is None:
        return state
    if (now - state.force_since).total_seconds() < state.force_max_h * 3600.0:
        return state
    return replace(state, mode=Mode.AUTO, force_since=None)


def as_on(value: Any) -> bool:
    """Whether a switch reads as on, whatever the entity spelled it.

    A boundary coercion, not a decision: `switch.*` says `"on"`, a `number` says
    `1.0` and a `binary_sensor` says `True`, and four types need the same answer
    out of all three. Anything unrecognised is **off**, because a state nobody
    can read must never be taken for a device that is running (INV-15).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0.0
    if value is None:
        return False
    return str(value).strip().lower() in {"on", "true", "yes", "1", "heat", "open"}


def recall(state: LoadState, key: str) -> Learned | None:
    """Return the numeric memory `key`, or `None` when nothing remembers it."""
    return state.learned.get(key)


def remember(state: LoadState, now: datetime, **values: float | None) -> LoadState:
    """Fold numeric memory into `LoadState.learned` (`design/DECISIONS.md` D-0200).

    A `None` **forgets** the key - which is what a sample whose sensor went away
    has to do, because a remembered number with no clock behind it is exactly
    the "decide against what we wrote" mistake INV-22 exists to stop.
    """
    learned = dict(state.learned)
    for key, value in values.items():
        if value is None:
            learned.pop(key, None)
        else:
            previous = learned.get(key)
            learned[key] = Learned(
                value=value, at=now, samples=1 if previous is None else previous.samples + 1
            )
    return replace(state, learned=learned)


@dataclass(frozen=True, slots=True)
class Transition:
    """A mode edge and what D7 must do about it (D4 §5.2)."""

    state: LoadState
    release: bool = False
    restore: bool = False
    reprovision: bool = False
    reason: str = ""


def transition(state: LoadState, to: Mode, now: datetime) -> Transition:
    """Move a load to `to` and say what the edge requires (D4 §5.2).

    Every edge that lets go releases first - nothing powerplan wrote survives
    into `off`, `observe` or `delegated` (INV-26), and the edge is the last write
    a site that is off will make - and every edge that takes charge again
    re-provisions and **restores**, never adopting what it finds (INV-29). Both
    undo only powerplan's own recorded writes (INV-27, `design/DECISIONS.md`
    D-0360).
    """
    if to is state.mode:
        return Transition(state=state, reason="unchanged")

    lets_go = to in {Mode.OFF, Mode.OBSERVE, Mode.DELEGATED}
    takes_over = state.mode in {Mode.OFF, Mode.OBSERVE, Mode.DELEGATED} and to in {
        Mode.AUTO,
        Mode.FORCE,
    }
    moved = replace(
        state,
        mode=to,
        force_since=now if to is Mode.FORCE else None,
    )
    return Transition(
        state=moved,
        release=lets_go,
        restore=takes_over,
        reprovision=takes_over,
        reason=f"{state.mode} → {to}",
    )


# --------------------------------------------------------------------------- #
# The Load
# --------------------------------------------------------------------------- #


class TypeLogic(Protocol):
    """The part of a `DeviceType` that one tick needs (D4 §4.6, §5.1).

    `DeviceType` in `types/base.py` extends this with the configuration half -
    the questionnaire, `derive()` and `build()` - which a tick never calls. Split
    because the `Load` is below the type registry in the import graph and needs
    only these three (`design/DECISIONS.md` D-0063).
    """

    def demand(self, load: Load, state: LoadState, ctx: LoadCtx) -> Demand:
        """Return what this load wants now and by when (D4 §4.1)."""

    def latch(self, load: Load, state: LoadState, ctx: LoadCtx) -> LoadState:
        """Set and clear the type's own latches from what the device said."""

    def kind_ctx(
        self, load: Load, state: LoadState, ctx: LoadCtx, *, grant: Grant | None, mode: Mode
    ) -> KindCtx:
        """Fill in what only the type knows: the target, the session, the limits."""


@dataclass(frozen=True, slots=True)
class Load:
    """One configured device: what it is, how it is steered, what it stores.

    Built by its `DeviceType` (`registry.build_load`), and frozen: everything
    that changes between ticks is in the `LoadState` the caller threads.
    """

    config: LoadConfig
    kind: ControlKind
    device_type: TypeLogic
    gate: GateConfig
    store: StoreModel | None = None

    # ------------------------------------------------------------- properties #

    @property
    def load_id(self) -> str:
        """The subentry id this load was built from (INV-50)."""
        return self.config.load_id

    def mode_now(self, state: LoadState, ctx: LoadCtx) -> Mode:
        """Return this load's effective mode this tick (PLAN §7 dec. 20)."""
        return effective_mode(state.mode, ctx.site_active)

    # ------------------------------------------------------------------ ticks #

    def observe(self, state: LoadState, ctx: LoadCtx) -> tuple[LoadState, Observation]:
        """Read the device and say what it wants (D4 §5.1).

        Pure reads plus the type's own latches - the session-done latch is set
        and cleared here, because it is a conclusion about what the device said.
        """
        state = expire_force(state, ctx.now)
        state = self.device_type.latch(self, state, ctx)
        stale = bool(self._stale_roles(ctx))
        if stale != (state.stale_since is not None):
            state = replace(state, stale_since=ctx.now if stale else None)
        demand = self.device_type.demand(self, state, ctx)
        connected = getattr(self.device_type, "connected", None)
        soc = getattr(self.device_type, "soc", None)
        legionella = getattr(self.device_type, "legionella", None)
        hygiene = legionella(self, state, ctx) if callable(legionella) else None
        return state, Observation(
            demand=demand,
            measured_w=ctx.reads.value(Role.POWER),
            health=self.health(state, ctx),
            connected=connected(ctx) if callable(connected) else None,
            soc=soc(ctx) if callable(soc) else None,
            legionella_due_at=None if hygiene is None else hygiene.due_at,
            legionella_last_completed=None if hygiene is None else hygiene.last_completed,
            legionella_active=None if hygiene is None else hygiene.active,
            legionella_in_progress=None if hygiene is None else hygiene.in_progress,
            legionella_at_risk=None if hygiene is None else hygiene.at_risk,
            cycle_state=None if state.cycle is None else state.cycle.notify_state,
            cycle_started_at=None if state.cycle is None else state.cycle.started_at,
        )

    def apply(self, grant: Grant, state: LoadState, ctx: LoadCtx) -> tuple[LoadState, ApplyResult]:
        """Turn a grant into the fewest, safest writes that achieve it (D4 §5.1).

        The only writer, and it writes through the gate (INV-20). A zero grant is
        not a shed: `grant.shed` is the only thing that says so (INV-25).
        """
        state = expire_force(state, ctx.now)
        mode = self.mode_now(state, ctx)
        kind_ctx = self.device_type.kind_ctx(self, state, ctx, grant=grant, mode=mode)

        quantised = self.kind.quantise(grant.w, kind_ctx)
        outcome = self.kind.command(quantised, grant, kind_ctx)
        if isinstance(outcome, Hold):
            return (
                state
                if _same_w(state.commanded_w, quantised.effective_w)
                else replace(state, commanded_w=quantised.effective_w),
                ApplyResult(
                    action=outcome.action,
                    value=quantised.value,
                    reason=outcome.reason,
                    reason_key=outcome.reason_key,
                    reason_params=outcome.reason_params,
                    effective_w=quantised.effective_w,
                    budget=ctx.budget,
                ),
            )

        decision = decide(
            outcome,
            current=ctx.reads.current_of(outcome.role),
            mode=mode,
            cfg=self.gate,
            state=state.gate,
            budget=ctx.budget,
            now=ctx.now,
            available=ctx.reads.available(outcome.role),
            current_at=ctx.reads.taken_at(outcome.role),
        )
        return self._settle(state, decision, ctx, quantised.effective_w, sheds=outcome.sheds)

    def release(
        self, state: LoadState, ctx: LoadCtx, reason: str = "released"
    ) -> tuple[LoadState, ApplyResult]:
        """Let go: undo what powerplan wrote and has on record (INV-26).

        The device goes back to what it held before powerplan's first write
        (`LoadState.prior`); a device powerplan never wrote to is left alone.
        Ignores the dwell clocks, the interval, the budget and the mode rows,
        because letting go is not a control action - but never sends a value the
        device already holds.
        """
        return self._hand_back(state, ctx, reason=reason, restoring=False)

    def restore(
        self, state: LoadState, ctx: LoadCtx, reason: str = "startup"
    ) -> tuple[LoadState, ApplyResult]:
        """Undo what powerplan wrote and has on record, as a start does (INV-27, INV-29).

        The same undo as `release()`, plus the restore dwell (no upward move
        within one dwell of it). Only powerplan's own recorded writes are undone
        and the device returns to what it held before them: a device powerplan
        never wrote to keeps whatever it holds, because that value is somebody
        else's (H.1 F-1: a charger's 10 A watchdog
        overridden to 32 A on every start, `design/DECISIONS.md` D-0360). The record
        is never powerplan's own write, so a start cannot walk a setpoint a band
        per restart.
        """
        return self._hand_back(state, ctx, reason=reason, restoring=True)

    def view_for_meter(self, state: LoadState, ctx: LoadCtx) -> ControlledView:
        """Return what D3 needs from this load this tick (D3 §5.8, INV-18)."""
        return ControlledView(
            load_id=self.config.load_id,
            measured_w=ctx.reads.value(Role.POWER),
            commanded_w=state.commanded_w,
            settling=state.gate.settling(ctx.now),
            phases=self.config.phase_names,
        )

    def health(self, state: LoadState, ctx: LoadCtx) -> Health:
        """Whether this load's bindings are answering (D4 §4.1, §8).

        A transient always carries its `since`: the gate's own clock for a write,
        the stale latch for a role that stopped answering (H.1 F-6, D-0362).
        """
        stale = self._stale_roles(ctx)
        gate = state.gate
        since = [at for at in (gate.transient_since, state.stale_since) if at is not None]
        return Health(
            ok=not gate.unhealthy and not stale,
            unhealthy=gate.unhealthy,
            failures=gate.failures,
            transient_since=min(since) if since else None,
            stale_roles=stale,
            last_error=gate.last_error,
        )

    @staticmethod
    def _stale_roles(ctx: LoadCtx) -> tuple[str, ...]:
        """Return the bound roles that cannot answer this tick, by name."""
        return tuple(
            str(role)
            for role, read in sorted(ctx.reads.roles.items())
            if not read.available
            or (read.reading is not None and read.reading.quality is not Quality.OK)
        )

    # --------------------------------------------------------------- internals #

    def _hand_back(
        self, state: LoadState, ctx: LoadCtx, *, reason: str, restoring: bool
    ) -> tuple[LoadState, ApplyResult]:
        """Return the shared body of `release()` and `restore()`: undo our record.

        With nothing on record there is nothing to undo - except a shed a build
        before the record left in the store (`shed_active`, no `prior`), which is
        handed back as the kind hands a device back.
        """
        undo: Command | Hold | None = _undo(state.prior)
        if undo is None and (state.prior or (state.shed_active and not restoring)):
            kind_ctx = self.device_type.kind_ctx(
                self, state, ctx, grant=None, mode=self.mode_now(state, ctx)
            )
            undo = self.kind.restore_command(kind_ctx)
        if undo is None:
            return replace(state, shed_active=False, shed_since=None), ApplyResult(
                action=Action.SAME,
                value=None,
                reason=f"{reason}: nothing of ours to undo",
                reason_key=ActionReason.NOTHING_TO_UNDO,
                budget=ctx.budget,
            )
        if isinstance(undo, Hold):
            return (
                replace(state, shed_active=False, shed_since=None),
                ApplyResult(
                    action=undo.action,
                    value=None,
                    reason=undo.reason,
                    reason_key=undo.reason_key,
                    reason_params=undo.reason_params,
                    budget=ctx.budget,
                ),
            )
        outcome = undo

        decision = decide(
            outcome,
            current=ctx.reads.current_of(outcome.role),
            mode=self.mode_now(state, ctx),
            cfg=self.gate,
            state=state.gate,
            budget=ctx.budget,
            now=ctx.now,
            available=ctx.reads.available(outcome.role),
            release=True,
        )
        state, result = self._settle(state, decision, ctx, None, sheds=False, undo=True)
        undone = decision.action in (Action.WRITTEN, Action.SAME)
        state = replace(
            state,
            shed_active=False,
            shed_since=None,
            prior={} if undone else state.prior,
            last_target_restore_at=ctx.now if restoring else state.last_target_restore_at,
        )
        return state, replace(result, reason=f"{reason}: {result.reason}")

    def _settle(
        self,
        state: LoadState,
        decision: Decision,
        ctx: LoadCtx,
        effective_w: float | None,
        *,
        sheds: bool,
        undo: bool = False,
    ) -> tuple[LoadState, ApplyResult]:
        """Fold one decision into the load's state (D4 §4.1, §7).

        A write that is not an undo adds to the record what each role it writes
        held just before, unless powerplan already wrote that role since it last
        let go - the record is never powerplan's own value (D-0360).
        """
        shed_active = state.shed_active
        shed_since = state.shed_since
        prior = state.prior
        if decision.written:
            shed_active = sheds
            shed_since = ctx.now if sheds else None
            if not undo and decision.command is not None:
                recorded = dict(prior)
                for write in decision.command.writes:
                    recorded.setdefault(str(write.role), ctx.reads.current_of(write.role))
                prior = recorded
        # Most ticks change none of these; skipping the copy then is the no-change
        # fast path of D7 §5.1 - the state is equal either way.
        updated = (
            state
            if decision.gate is state.gate
            and shed_active is state.shed_active
            and shed_since is state.shed_since
            and prior is state.prior
            and _same_w(state.commanded_w, effective_w)
            else replace(
                state,
                gate=decision.gate,
                shed_active=shed_active,
                shed_since=shed_since,
                commanded_w=effective_w,
                prior=prior,
            )
        )
        return updated, ApplyResult(
            action=decision.action,
            value=decision.value,
            reason=decision.reason,
            reason_key=decision.reason_key,
            reason_params=decision.reason_params,
            command=decision.command,
            blocking=decision.blocking,
            verify_at=decision.verify_at,
            effective_w=effective_w,
            budget=decision.budget,
            current=decision.current,
        )


def _undo(prior: Mapping[str, Value | None]) -> Command | None:
    """Return the command that puts every recorded role back as it was (INV-26, D-0360).

    `None` when there is nothing on record, or when a role's earlier value could
    not be read - the caller then hands the device back as its kind does.
    """
    if not prior or any(value is None for value in prior.values()):
        return None
    writes = tuple(Write(Role(role), value) for role, value in prior.items() if value is not None)
    return Command(
        writes=writes,
        reason=f"back to {writes[0].value}, as it was before powerplan wrote to it",
        reason_key=ActionReason.BACK_TO_PRIOR,
        urgent=True,
    )


def gate_config(
    kind: ControlKind, config: LoadConfig, *, transient_grace_s: float = TRANSIENT_GRACE_S
) -> GateConfig:
    """Return the gate configuration for one load, from its kind and its own floor.

    A load's `command_min_interval` may **raise** the kind's interval and never
    lower it; the transport comes from the profile's quirks, which arrive in the
    config as data (D4 §5.10).
    """
    return config_for(
        kind,
        transport=config.transport,
        command_min_interval_s=config.command_min_interval_s,
        transient_grace_s=transient_grace_s,
    )


def _same_w(old: float | None, new: float | None) -> bool:
    """Return whether storing `new` would leave `old` exactly as it is - -0.0 is not 0.0."""
    if old is None or new is None:
        return old is new
    return old == new and copysign(1.0, old) == copysign(1.0, new)
