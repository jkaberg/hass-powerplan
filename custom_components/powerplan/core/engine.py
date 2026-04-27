"""D7 - the engine: one tick as a pure function of what it was given (HLD §6.7).

    tick(state, inputs) -> (state, snapshot, effects)
    plan(state, inputs) -> (state, report, effects)

This is the one function the Home Assistant runtime and the scenario runner both
call, and the only place the order of the two loops is written down. It reads
nothing - every value arrives in `Inputs` - and it writes nothing: every device
write leaves as a `LoadCommand` on `Effects` for `writegate.py` to perform
(INV-2, INV-3, INV-20).

**The sacred order** (D7 §5.1): site switch → sample the meter → closed windows
into the tariff → ceiling → budget → ladder → demands → allocate → apply →
warnings → snapshot. The ladder runs before the allocator because `allocate()`
takes the stage as an input and escalation is immediate (D6 §5.4).

**Four invariants live here or nowhere.**

* **INV-43** - no tick at the window boundary. The register report closes the window
  (D3), and a tick at `HH:00:00` saw the whole of the departing window's energy as the
  arriving one's on the ancestor controller. The trigger half is the runtime's; the tick
  refuses to be silent about it, and says so in `Snapshot.reasons`.
* **INV-44** - publish always. Off, observing, frozen, in safe mode or throwing:
  a snapshot comes back from every tick. A controller that goes quiet when
  switched off cannot be evaluated before it is switched on.
* **INV-45** - one load's exception never stops the tick. The load is held
  `unhealthy`, its previous grant kept, and every other load is decided.
* **INV-15 / INV-17** - blindness freezes the tick. A stale meter or a window
  seam holds every grant where it was; it never opens a gate.

**What is not here.** No algorithm of D1–D6: the tick composes them. No I/O and
no accounting in the tick - the ledger closes its slots in `plan()` (INV-68), and
everything network-bound happens in the planning loop or in a provider (INV-46).
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import Enum, StrEnum
from types import UnionType
from typing import (
    Any,
    Final,
    Literal,
    Protocol,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from .allocation import (
    AllocCfg,
    AllocCtx,
    AllocReport,
    AllocState,
    Baseline,
    Budget,
    BudgetCfg,
    Constraint,
    ContractedPowerLimit,
    Grants,
    HardLimits,
    Ladder,
    LadderCfg,
    LadderState,
    PhaseLimit,
    SiteFuse,
    allocate,
    frozen_for,
    is_outlier,
    measured_w,
    pi_close,
    reason_key,
    reserved_w,
)
from .allocation import (
    budget as build_budget,
)
from .loads import (
    Action,
    ApplyResult,
    ComfortState,
    Demand,
    Health,
    Load,
    LoadCtx,
    LoadState,
    Mode,
    Reads,
    Role,
    Urgency,
    effective_mode,
    transition,
)
from .loads.gate import Decision, GateState, TransportBudget
from .loads.targets import CalendarEvent, PresenceMode
from .metering import (
    ControlledView,
    ElectricalProfile,
    MeterSample,
    MeterSnapshot,
    WindowMeter,
    WindowState,
    window_bounds,
)
from .model import (
    Carrier,
    Confidence,
    Grant,
    Money,
    Plan,
    PlanMode,
    PriceCurve,
    Snapshot,
)
from .pricing import Event, HysteresisPolicy
from .strategies import Curves, Forecasts, LoadView, SiteContext, plan_all
from .tariffs import (
    AUTO,
    Advice,
    Ceiling,
    HardLimit,
    Level,
    Target,
    TariffModel,
    TariffState,
    eps_for_window,
)

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "AccountingClose",
    "AccountingHook",
    "AccountingStatus",
    "CarrierPrices",
    "Effects",
    "Engine",
    "EngineCfg",
    "EngineHealth",
    "EngineState",
    "EventKind",
    "EventsState",
    "ForecastStatus",
    "HaEvent",
    "HealthStatus",
    "Inputs",
    "Knobs",
    "LoadCommand",
    "LoadLatches",
    "LoadReads",
    "LoadStatus",
    "Notification",
    "PeakEntry",
    "PeakWarnState",
    "PlanReport",
    "PlanStatus",
    "PlansState",
    "PriceStatus",
    "RepairIssue",
    "RuntimeState",
    "Section",
    "SiteConfig",
    "SitePath",
    "SiteStatus",
    "SiteWarning",
    "SnapshotSchema",
    "TariffStatus",
]

#: The `Snapshot.schema` this engine publishes. D8 reads it; bump it when a
#: section changes shape (D7 §4.1, the golden in `tests/golden/`).
SnapshotSchema: int = 1

#: What the peak warning's EMA is worth after this long without a tick: a gap
#: wider than this restarts the average rather than extrapolating a dead house.
_EMA_RESET_S = 1800.0


class Section(StrEnum):
    """The store's sections, as `Effects.store_dirty` names them (D7 §2, §7).

    The same ten names as `storage.py`'s `Section`, spelled again here because
    `storage.py` imports Home Assistant and `core/` may not (INV-2). A test
    compares the two lists (`design/DECISIONS.md` D-0231).
    """

    METER = "meter"
    TARIFF = "tariff"
    PRICES = "prices"
    PLANS = "plans"
    LOADS = "loads"
    ALLOC = "alloc"
    FORECASTS = "forecasts"
    ACCOUNTING = "accounting"
    EVENTS = "events"
    RUNTIME = "runtime"


class SitePath(StrEnum):
    """Which parts of the engine a site switched on (HLD §4, D8 §5)."""

    FULL = "full"
    PRICE_ONLY = "price_only"
    FUSE_ONLY = "fuse_only"


class EngineHealth(StrEnum):
    """How the engine itself is doing (D7 §2's error budget, §8)."""

    OK = "ok"
    FAILING = "failing"
    SAFE_MODE = "safe_mode"


class EventKind(StrEnum):
    """The HA events a tick or a cycle implies (D8 §5.6; the runtime prefixes them)."""

    STAGE_CHANGED = "stage_changed"
    BREACH = "breach"
    COMFORT_VIOLATION = "comfort_violation"
    PEAK_WARNING = "peak_warning"
    DEADLINE_AT_RISK = "deadline_at_risk"
    PLAN_ADOPTED = "plan_adopted"
    DEVICE_UNHEALTHY = "device_unhealthy"
    MONTH_CLOSED = "month_closed"
    SAFE_MODE = "safe_mode"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EngineCfg:
    """The engine's own knobs - Advanced only (D7 §6).

    `tick_min_interval_s`, `heartbeat_s` and `plan_interval_min` are the
    *runtime's* and deliberately absent: nothing in a pure tick can read
    a clock it does not get in `Inputs`.
    """

    warn_horizon_h: float = 3.0
    warn_fraction: float = 0.95
    clear_fraction: float = 0.85
    safe_mode_after_failures: int = 3
    tick_budget_ms: float = 50.0
    #: The time constant of the peak warning's EMA of uncontrolled power (§5.4).
    ema_tau_s: float = 900.0
    #: `Snapshot.reasons` is a trail a human reads, not a log (D7 §4.1).
    max_reasons: int = 20


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """One site as the tick needs it: what it is, not what it reads (D7 §3).

    Everything here is materialised configuration (INV-66) and changes only on a
    reload; the live knobs are in `Knobs` and are read every tick (INV-47).
    """

    site_id: str
    tz: tzinfo
    electrical: ElectricalProfile
    name: str = ""
    path: SitePath = SitePath.FULL
    currency: str = "NOK"
    window_min: int = 60
    horizon_h: float = 48.0
    budget: BudgetCfg = field(default_factory=BudgetCfg)
    alloc: AllocCfg = field(default_factory=AllocCfg)
    ladder: LadderCfg = field(default_factory=LadderCfg)
    engine: EngineCfg = field(default_factory=EngineCfg)
    hysteresis: HysteresisPolicy = field(default_factory=HysteresisPolicy)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Knobs:
    """Every knob the tick reads, read live (INV-47).

    Never cached in `EngineState`: a target the household just lowered takes
    effect on the next tick and not on the next window (INV-12), and a mode
    changed by hand is honoured on the tick that sees it (D4 §5.2).
    """

    active: bool = True
    target: Target = AUTO
    risk: float | None = None
    eps_base_kwh: float | None = None
    presence: PresenceMode | None = None
    modes: Mapping[str, Mode] = field(default_factory=dict)
    force_max_h: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadReads:
    """What one load's bound entities say this tick (D7 §3, D4 §5.1)."""

    reads: Reads
    calendar: tuple[CalendarEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything the tick may read, and nothing else (D7 §3).

    Assembled by `runtime.py` from `hass.states`, the stores and the site's
    domain objects - which is the only place a state is read (INV-3). A tick that
    wanted a value not in here would have to do I/O, and that is what INV-46
    forbids.
    """

    now: datetime
    site: SiteConfig
    meter: MeterSample
    loads: Mapping[str, LoadReads] = field(default_factory=dict)
    knobs: Knobs = field(default_factory=Knobs)
    curves: Curves | None = None
    forecasts: Forecasts | None = None
    events: tuple[Event, ...] = ()
    #: The site's ambient reads (a weather entity, an indoor sensor). D7 §3
    #: amended: a store model needs them and they are not per load.
    outdoor_c: float | None = None
    indoor_c: float | None = None
    #: The WriteGate's token buckets, which outlive one tick (INV-58). Read live,
    #: like a knob: the executor owns the bucket (D7 §3 amended).
    transport: TransportBudget = field(default_factory=TransportBudget.empty)
    #: What woke this tick, for the trail. The runtime's vocabulary (D7 §5.3).
    trigger: str = "tick"


# --------------------------------------------------------------------------- #
# Effects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LoadCommand:
    """One load's decision, for the executor to perform (D7 §5.6, INV-3).

    The pure `Decision` of `core/loads/gate.py` plus the load it belongs to;
    `writegate.py` wraps it in an `Actuation` with the load's profile and gate
    configuration, which only the runtime has. The order of `Effects.commands`
    is the priority order the transport budget was spent in (INV-58).
    """

    load_id: str
    decision: Decision


@dataclass(frozen=True, slots=True)
class HaEvent:
    """One event for the HA bus; the runtime fires `powerplan_<kind>` (D8 §5.6)."""

    kind: EventKind
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Notification:
    """One thing worth telling the household, for D8's per-category policy.

    `key` is the de-duplication key: the same key twice is the same notification,
    which is how "at most one per window" is kept (D7 §5.4).
    """

    category: str
    key: str
    params: Mapping[str, Any] = field(default_factory=dict)
    severity: Literal["info", "warn"] = "info"


@dataclass(frozen=True, slots=True)
class RepairIssue:
    """A repair issue to raise or to clear (D7 §8, INV-53)."""

    issue_id: str
    translation_key: str
    params: Mapping[str, Any] = field(default_factory=dict)
    severe: bool = False
    active: bool = True


@dataclass(frozen=True, slots=True)
class Effects:
    """Everything the tick wants done, executed after it returns (D7 §3, §5.6).

    `store_dirty` goes through the 5 s throttle; `store_now` is the at-once set -
    an anchor change and every lifecycle edge (INV-14, D7 §7 amended: only the
    engine knows the anchor moved).
    """

    commands: tuple[LoadCommand, ...] = ()
    ha_events: tuple[HaEvent, ...] = ()
    store_dirty: frozenset[Section] = frozenset()
    store_now: frozenset[Section] = frozenset()
    notifications: tuple[Notification, ...] = ()
    repairs: tuple[RepairIssue, ...] = ()


# --------------------------------------------------------------------------- #
# Snapshot sections (D7 §4.1) - the contract with D8 and D9
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SiteStatus:
    """What the site itself is doing this tick (D7 §4.1)."""

    site_id: str
    name: str
    path: SitePath
    active: bool
    safe_mode: bool
    presence: PresenceMode | None
    target_kw: float | None
    risk: float
    trigger: str
    window_min: int


@dataclass(frozen=True, slots=True)
class PeakEntry:
    """One day's contribution to the period's metric (D2 §4's `DayRec`)."""

    day: date
    kw: float
    estimated: bool


@dataclass(frozen=True, slots=True)
class TariffStatus:
    """Where the capacity axis stands (D7 §4.1, D2 §4)."""

    version_id: str
    eligible: bool
    weight: float
    ceiling_kwh: float
    ceiling_reason: str
    slack_kwh: float | None
    free_ride: bool
    metric_kw: float
    level: Level
    projected_level: Level
    top: tuple[PeakEntry, ...]
    advice: tuple[Advice, ...]
    period_key: str
    period_start: datetime
    period_end: datetime
    hard_limit_w: float | None


@dataclass(frozen=True, slots=True)
class CarrierPrices:
    """One carrier's prices as a dashboard reads them (D1 §4)."""

    carrier: Carrier
    currency: str
    now: Decimal | None
    next: Decimal | None
    min_today: Decimal | None
    max_today: Decimal | None
    mean_today: Decimal | None
    spread_today: Decimal
    confidence: Confidence | None
    coverage_h: float


@dataclass(frozen=True, slots=True)
class PriceStatus:
    """The price axis as of this tick (D7 §4.1, D1 §4)."""

    carriers: Mapping[Carrier, CarrierPrices] = field(default_factory=dict)
    tomorrow_available: bool = False
    built_at: datetime | None = None
    sources: tuple[str, ...] = ()
    stale: bool = True


@dataclass(frozen=True, slots=True)
class PlanStatus:
    """One load's adopted plan, summarised (D7 §4.1, D5 §4)."""

    load_id: str
    strategy: str
    mode: PlanMode
    cap_now_w: float | None
    next_start: datetime | None
    planned_kwh: float
    required_kwh: float | None
    cost: Money
    covered: bool
    coverage: float
    deadline: datetime | None
    confidence: Confidence
    built_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class LoadLatches:
    """The latches D7 persists for one load (D4 §7)."""

    shed_active: bool
    shed_since: datetime | None
    session_done: bool
    session_done_reason: str | None
    force_since: datetime | None
    force_max_h: float
    provisioned: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadStatus:
    """One load as of this tick (D7 §4.1, D4 §4.1).

    `held` is the blindness and isolation flag: the grant is last tick's, because
    this tick either could not see (INV-15, INV-17) or the load's own logic threw
    (INV-45).
    """

    load_id: str
    name: str
    type_key: str
    priority: int
    mode: Mode
    configured_mode: Mode
    granted_w: float
    measured_w: float | None
    reserved_w: float
    commanded_w: float | None
    demand: Demand
    comfort: ComfortState | None
    shed: bool
    shed_reason: str | None
    stage: int
    blunt: bool
    capped_by: tuple[str, ...]
    action: Action
    action_reason: str
    health: Health
    latches: LoadLatches
    learned: Mapping[str, float]
    held: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class ForecastStatus:
    """What D10 was able to say (D7 §4.1; WP5.1 fills the fit half)."""

    available: bool = False
    outdoor_c: float | None = None
    surplus_w: float | None = None
    baseline_w: float | None = None
    baseline_ready: bool = False
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class AccountingStatus:
    """What the ledger says, month to date (D7 §4.1, D11).

    Changes once per closed slot, never inside a tick (INV-68). WP0.10a fills
    `cost`, `savings` and the per-load rows through the `AccountingHook`.
    """

    slots_closed: int = 0
    closed_to: datetime | None = None
    month_key: str | None = None
    cost: Money | None = None
    savings: Money | None = None
    confidence: str = "none"
    per_load: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SiteWarning:
    """One thing the household can act on that the controller cannot (D7 §5.4).

    Spelled `SiteWarning` and not `Warning` as D7 §4.1 writes it: `Warning` is a
    builtin and shadowing it in the type the whole surface imports is a trap
    (`design/DECISIONS.md` D-0232, D7 §4.1 amended).
    """

    kind: Literal["peak", "peak_uncontrolled", "deadline", "comfort"]
    key: str
    window_start: datetime | None
    window_end: datetime | None
    expected_kwh: float
    ceiling_kwh: float
    drivers: tuple[tuple[str, float], ...] = ()
    advice: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Why this tick can or cannot be trusted (D7 §4.1 amended, §8)."""

    engine: EngineHealth
    failures: int
    unhealthy_loads: tuple[str, ...]
    failed_loads: Mapping[str, str]
    frozen_reason: str | None
    stale_meter: bool
    degraded_meter: bool
    prices_stale: bool
    forecasts_available: bool
    tick_ms: float
    over_budget: bool
    boundary_tick: bool


# --------------------------------------------------------------------------- #
# Engine state (D7 §4.2) - exactly the store sections
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PeakWarnState:
    """The peak warning's memory: the EMA and the windows already warned (§5.4)."""

    ema_w: float | None = None
    ema_at: datetime | None = None
    warned: tuple[datetime, ...] = ()
    #: The key of the live (this-window) warning being shown, for edge detection.
    live: str | None = None


@dataclass(frozen=True, slots=True)
class EventsState:
    """The `events` section: what has already been said (D7 §2).

    One edge per key, holding the value last published, so an event fires on a
    change and not on a repetition.
    """

    schema: int = 1
    edges: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """The `runtime` section: the tick counter, the counters, the window's stats.

    `safe_mode` is deliberately **not** persisted: a restart clears it (D7 §2),
    and the failure counter that produced it resets on the first tick that
    succeeds.
    """

    schema: int = 1
    tick_no: int = 0
    last_tick_at: datetime | None = None
    last_plan_at: datetime | None = None
    failures: int = 0
    load_failures: Mapping[str, int] = field(default_factory=dict)
    closed_to: datetime | None = None
    slots_closed: int = 0
    peak: PeakWarnState = field(default_factory=PeakWarnState)
    #: This window's uncontrolled statistics, for the PI's outlier gate (D6 §5.1).
    window_start: datetime | None = None
    uc_peak_w: float = 0.0
    uc_mean_w: float = 0.0
    uc_samples: int = 0
    #: How long the measured total has been over the contracted limit (D2 §5.8).
    over_since: datetime | None = None
    #: Entered after `safe_mode_after_failures` consecutive engine exceptions;
    #: never written to the store, so a restart clears it (D7 §2).
    safe_mode: bool = False


@dataclass(frozen=True, slots=True)
class PlansState:
    """The `plans` section: the adopted plans and when they were adopted (D5 §5.9)."""

    schema: int = 1
    plans: Mapping[str, Plan] = field(default_factory=dict)
    built_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EngineState:
    """Everything the engine remembers between ticks (D7 §4.2).

    Exactly the store sections, plus two caches that are deliberately **not**
    persisted and are marked as such: `grants` (last tick's, which a restart
    invalidates anyway because setup releases every load - INV-26, INV-48) and
    `last_snapshot` (what a failing tick republishes, D7 §8).

    `prices`, `forecasts` and `accounting` are carried as opaque mappings: D1,
    D10 and D11 own their shape and one save writes the whole document, so the
    engine must not lose a section it does not read (D7 §2).
    """

    meter: WindowState | None = None
    tariff: TariffState | None = None
    prices: Mapping[str, Any] = field(default_factory=dict)
    plans: PlansState = field(default_factory=PlansState)
    loads: Mapping[str, LoadState] = field(default_factory=dict)
    alloc: AllocState = field(default_factory=AllocState)
    forecasts: Mapping[str, Any] = field(default_factory=dict)
    accounting: Mapping[str, Any] = field(default_factory=dict)
    events: EventsState = field(default_factory=EventsState)
    runtime: RuntimeState = field(default_factory=RuntimeState)
    grants: Mapping[str, Grant] = field(default_factory=dict)
    last_snapshot: Snapshot | None = None

    def to_sections(self) -> dict[str, Any]:
        """Return the JSON-able document `storage.py` writes, by section (D7 §7)."""
        return {
            Section.METER.value: _encode(self.meter),
            Section.TARIFF.value: _encode(self.tariff),
            Section.PRICES.value: dict(self.prices),
            Section.PLANS.value: _encode(self.plans),
            Section.LOADS.value: _encode(self.loads),
            Section.ALLOC.value: _encode(self.alloc),
            Section.FORECASTS.value: dict(self.forecasts),
            Section.ACCOUNTING.value: dict(self.accounting),
            Section.EVENTS.value: _encode(self.events),
            Section.RUNTIME.value: _encode(replace(self.runtime, safe_mode=False)),
        }

    @classmethod
    def from_sections(cls, data: Mapping[str, Any]) -> EngineState:
        """Return the state `to_sections()` wrote (D7 §7).

        A section that is absent or empty starts fresh - a first start and a
        downgrade that dropped a section are the same thing to the engine, and
        both are safe: setup releases every load before the first tick (INV-48).
        """

        def section(name: Section) -> Any:
            return data.get(name.value) or None

        return cls(
            meter=_decode(WindowState, section(Section.METER)),
            tariff=_decode(TariffState, section(Section.TARIFF)),
            prices=dict(data.get(Section.PRICES.value) or {}),
            plans=_decode(PlansState, section(Section.PLANS)) or PlansState(),
            loads=_decode(Mapping[str, LoadState], data.get(Section.LOADS.value) or {}) or {},
            alloc=_decode(AllocState, section(Section.ALLOC)) or AllocState(),
            forecasts=dict(data.get(Section.FORECASTS.value) or {}),
            accounting=dict(data.get(Section.ACCOUNTING.value) or {}),
            events=_decode(EventsState, section(Section.EVENTS)) or EventsState(),
            runtime=_decode(RuntimeState, section(Section.RUNTIME)) or RuntimeState(),
        )


# --------------------------------------------------------------------------- #
# The accounting hook (D7 §9 16) - WP0.10a wires the real one
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AccountingClose:
    """What D11 answers when a price slot is closed (D7 §5.2)."""

    slot_start: datetime
    slot_end: datetime
    month_closed: str | None = None
    state: Mapping[str, Any] = field(default_factory=dict)
    status: AccountingStatus | None = None
    reason: str = ""


class AccountingHook(Protocol):
    """The one call `plan()` makes into D11 (INV-68, D7 §9 16).

    D11's `core/accounting/close.py::close_slot(ClosedSlot, CloseCtx)` (WP0.10a,
    on another branch) is adapted to this by the runtime, which is what keeps
    the engine from importing `core/accounting` at all: the tick must not be able
    to reach the ledger even by accident, and a `Protocol` taken as a constructor
    argument cannot be reached from `tick()`.
    """

    def close_slot(self, start: datetime, end: datetime, *, now: datetime) -> AccountingClose:
        """Close the price slot `[start, end)` and return what it changed."""
        ...


@dataclass(frozen=True, slots=True)
class PlanReport:
    """What one planning cycle did (D7 §3, §5.2)."""

    at: datetime
    duration_ms: float
    adopted: tuple[str, ...] = ()
    plans: Mapping[str, Plan] = field(default_factory=dict)
    uncovered: tuple[str, ...] = ()
    slots_closed: int = 0
    month_closed: str | None = None
    failed: Mapping[str, str] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class Engine:
    """The two loops, in the right order, over one site's domain objects.

    The domain objects are collaborators, not state: `WindowMeter`, `Evaluator`
    and the constraints each own their own internals (D3, D2, D6 built them as
    mutable objects), and `EngineState` carries the persistable projection of
    each - `WindowState`, `TariffState`, `AllocState` (`design/DECISIONS.md`
    D-0230). Everything the engine itself remembers is threaded through
    `EngineState`, so the same state and the same inputs give the same tick.
    """

    def __init__(
        self,
        site: SiteConfig,
        meter: WindowMeter,
        tariff: TariffModel,
        loads: Sequence[Load],
        *,
        constraints: Sequence[Constraint] | None = None,
        accounting: AccountingHook | None = None,
    ) -> None:
        """Wire one site. Nothing here reads a store or a state (INV-3)."""
        self.site = site
        self._meter = meter
        self._tariff = tariff
        self._loads: tuple[Load, ...] = tuple(
            sorted(loads, key=lambda load: (-load.config.priority, load.load_id))
        )
        self._constraints: tuple[Constraint, ...] = (
            (SiteFuse(site.electrical.fuse_w()), PhaseLimit(site.electrical))
            if constraints is None
            else tuple(constraints)
        )
        self._accounting = accounting

    @property
    def loads(self) -> tuple[Load, ...]:
        """The site's loads, in the order the walk and the budget spend them."""
        return self._loads

    # ----------------------------------------------------------------- the tick #

    def tick(self, state: EngineState, inputs: Inputs) -> tuple[EngineState, Snapshot, Effects]:
        """Run one control loop and publish, whatever happens (D7 §5.1, INV-44).

        The order of §5.1 is the order of `_run`; this wrapper is the error
        budget of §2: an exception outside a load is the engine's own bug, so the
        counter moves, the previous snapshot is republished, and three in a row
        put the site in safe mode with every load released (INV-64).
        """
        started = time.perf_counter()
        try:
            return self._run(state, inputs, started)
        except Exception as err:
            _LOGGER.exception("engine tick failed on tick %s", state.runtime.tick_no + 1)
            return self._engine_failed(state, inputs, err, started)

    def _run(  # noqa: PLR0912, PLR0915 - D7 §5.1's eleven steps, kept in one place and in order
        self, state: EngineState, inputs: Inputs, started: float
    ) -> tuple[EngineState, Snapshot, Effects]:
        """Run the eleven steps of D7 §5.1, in order."""
        now = inputs.now
        site = inputs.site
        cfg = site.engine
        knobs = inputs.knobs
        reasons: list[str] = []
        events: list[HaEvent] = []
        notes: list[Notification] = []
        repairs: list[RepairIssue] = []
        commands: list[LoadCommand] = []
        failed: dict[str, str] = {}
        edges = dict(state.events.edges)

        # -- 1. is the site on? ------------------------------------------- #
        safe_mode = state.runtime.safe_mode
        active = knobs.active and not safe_mode
        if safe_mode:
            reasons.append("safe mode: every load released and observing until acknowledged")
        elif not knobs.active:
            reasons.append("site off: every load released and observing, decisions published")
        boundary = _is_boundary_tick(now, site.window_min, site.tz)
        if boundary:
            reasons.append(
                f"INV-43: ticked at the window boundary {now.isoformat()} — the register "
                "report closes the window, not the clock"
            )
            _LOGGER.warning(
                "tick at the window boundary %s: no wall-clock trigger may run a full "
                "tick at the boundary (INV-43)",
                now.isoformat(),
            )

        # -- 1b. mode edges, read live (INV-47), released before anything else #
        load_states, edge_commands, edge_dirty = self._mode_edges(state, inputs, active, reasons)
        commands.extend(edge_commands)

        # -- 2. the meter -------------------------------------------------- #
        views = self._views(load_states, inputs, active, failed)
        meter = self._meter.sample(now, inputs.meter, tuple(views.values()))
        frozen = frozen_for(meter)
        if frozen:
            reasons.append(
                f"frozen: {meter.frozen_reason} — every grant held where it was (INV-15, INV-17)"
            )

        # -- 3. closed windows into the tariff, then the PI ---------------- #
        runtime = _window_stats(state.runtime, meter)
        pi = state.alloc.pi
        if meter.health.degraded:
            pi = pi.saw_degraded()
        if is_outlier(
            runtime.uc_peak_w, runtime.uc_mean_w, meter.sigma_uncontrolled_w, site.budget.outlier_k
        ):
            pi = pi.saw_outlier()
        for window in meter.closed:
            self._tariff.record_window(window)
            pi = pi_close(pi, window.kwh, self._closed_ceiling_kwh(window, knobs), site.budget)
            reasons.append(
                f"window {window.start_utc.isoformat()} closed at {window.kwh:.3f} kWh "
                f"({window.confidence})"
            )
        if meter.closed:
            last = meter.closed[-1]
            self._meter.ack_closed(last.start_utc + timedelta(minutes=last.window_min))

        # -- 4. the ceiling ------------------------------------------------ #
        eps_base = site.budget.eps_base_kwh if knobs.eps_base_kwh is None else knobs.eps_base_kwh
        eps_kwh = eps_for_window(eps_base, meter.window_min)
        risk = self._tariff.state().risk if knobs.risk is None else knobs.risk
        ceiling = self._tariff.ceiling_kwh(now, knobs.target, risk, eps_kwh)

        # -- 5. the budget ------------------------------------------------- #
        hard, runtime = self._hard_limits(now, meter, runtime)
        budget = build_budget(
            ceiling, meter, hard.p_hard_w(), pi, site.budget, _baseline(inputs.forecasts)
        )
        reasons.append(
            f"budget: ceiling {budget.ceiling_kwh:.2f} kWh − used {budget.used_kwh:.2f} − "
            f"reserve {budget.reserve_kwh:.2f} → allow {budget.p_allow_w:.0f} W"
        )

        # -- 6. the ladder ------------------------------------------------- #
        ladder = Ladder(state.alloc.ladder)
        target_kwh = self._target_kwh(now, knobs.target, meter.window_min)
        if frozen:
            ladder_state = ladder.state
        else:
            ladder_state = ladder.update(
                budget,
                p_total_w=_smooth_w(meter),
                hard=hard,
                target_kwh=target_kwh,
                now=now,
                cfg=site.ladder,
            )
        if (
            ladder_state.stage >= 1
            and reason_key(ladder_state.reason) == "projection"
            and not frozen
        ):
            pi = pi.saw_binding()

        # -- 7. the demands, per-load isolated (INV-45) -------------------- #
        load_states, observations, load_views = self._observe(
            load_states, inputs, active, failed, ladder_state
        )

        # -- 8. allocate --------------------------------------------------- #
        ctx = AllocCtx(
            now=now,
            meter=meter,
            budget=budget,
            electrical=site.electrical,
            loads=load_views,
            plans=state.plans.plans,
            views=views,
            previous=state.grants,
            stage=ladder_state.stage,
            blunt=ladder_state.blunt,
            frozen=frozen,
            hard=hard,
            marginal_cost=self._tariff.marginal_cost,
        )
        grants, report, alloc_state = allocate(
            ctx,
            (*self._constraints, ContractedPowerLimit(hard.contracted)),
            site.alloc,
            replace(state.alloc, pi=pi, ladder=ladder_state),
        )
        reasons.extend(_grant_reasons(load_views, grants))

        # -- 9. apply ------------------------------------------------------ #
        if frozen:
            # Blindness never opens a gate (INV-15, INV-17): the previous grants
            # stand and nothing is written, not even a re-assertion of them.
            results: dict[str, ApplyResult] = {}
        else:
            load_states, results, applied = self._apply(
                load_states, inputs, active, failed, grants, ladder_state, state.plans.plans
            )
            commands.extend(applied)

        # -- 10. the warnings ---------------------------------------------- #
        runtime = _ema(runtime, meter, frozen, cfg)
        warnings, runtime, warn_events, warn_notes = self._warnings(
            runtime, inputs, budget, ceiling, meter, load_views, state.plans.plans
        )
        events.extend(warn_events)
        notes.extend(warn_notes)

        # -- 11. the snapshot, the edges, the dirty set --------------------- #
        duration_ms = (time.perf_counter() - started) * 1000.0
        over_budget = duration_ms > cfg.tick_budget_ms
        if over_budget:
            reasons.append(
                f"tick took {duration_ms:.1f} ms, over the {cfg.tick_budget_ms:.0f} ms budget"
            )

        runtime = replace(
            runtime,
            tick_no=state.runtime.tick_no + 1,
            last_tick_at=now,
            failures=0,
            load_failures=_bumped(state.runtime.load_failures, failed),
        )
        engine_health = EngineHealth.SAFE_MODE if safe_mode else EngineHealth.OK
        health = HealthStatus(
            engine=engine_health,
            failures=0,
            unhealthy_loads=tuple(
                load_id for load_id, obs in observations.items() if obs.health.unhealthy
            ),
            failed_loads=dict(failed),
            frozen_reason=meter.frozen_reason,
            stale_meter=meter.health.stale,
            degraded_meter=meter.health.degraded,
            prices_stale=inputs.curves is None,
            forecasts_available=inputs.forecasts is not None,
            tick_ms=duration_ms,
            over_budget=over_budget,
            boundary_tick=boundary,
        )
        events.extend(_domain_events(edges, ladder_state, report, observations, failed))
        for load_id, error in failed.items():
            reasons.append(f"{load_id}: failed and held — {error} (INV-45)")
            repairs.append(
                RepairIssue(
                    issue_id=f"load_error_{load_id}",
                    translation_key="load_error",
                    params={"load": load_id, "error": error},
                )
            )
        notes.extend(_level_notification(edges, self._tariff, budget))

        snapshot = Snapshot(
            schema=SnapshotSchema,
            at=now,
            tick_no=runtime.tick_no,
            duration_ms=duration_ms,
            site=SiteStatus(
                site_id=site.site_id,
                name=site.name,
                path=site.path,
                active=active,
                safe_mode=safe_mode,
                presence=knobs.presence,
                target_kw=knobs.target.kw,
                risk=risk,
                trigger=inputs.trigger,
                window_min=meter.window_min,
            ),
            meter=meter,
            budget=budget,
            ladder=ladder_state,
            tariff=self._tariff_status(now, ceiling, budget, hard),
            prices=_price_status(inputs, site),
            plans=_plan_statuses(state.plans.plans, now),
            loads=self._load_statuses(
                load_states, inputs, grants, observations, results, views, failed, frozen
            ),
            alloc=report,
            forecasts=_forecast_status(inputs),
            accounting=_accounting_status(state),
            warnings=warnings,
            reasons=_trail(reasons, cfg.max_reasons),
            health=health,
        )
        _LOGGER.debug(
            "tick %s: stage %s, allow %.0f W, used %.2f/%.2f kWh, %s commands in %.1f ms",
            snapshot.tick_no,
            ladder_state.stage,
            budget.p_allow_w,
            budget.used_kwh,
            budget.ceiling_kwh,
            len(commands),
            duration_ms,
        )

        new_state = replace(
            state,
            meter=self._meter.state(),
            tariff=self._tariff.state(),
            loads=load_states,
            alloc=alloc_state,
            grants=grants,
            events=EventsState(schema=state.events.schema, edges=edges),
            runtime=runtime,
            last_snapshot=snapshot,
        )
        effects = Effects(
            commands=tuple(commands),
            ha_events=tuple(events),
            store_dirty=_dirty(state, new_state) | edge_dirty,
            store_now=_at_once(state, new_state, meter),
            notifications=tuple(notes),
            repairs=tuple(repairs),
        )
        return new_state, snapshot, effects

    # ------------------------------------------------------- the failing tick #

    def _engine_failed(
        self, state: EngineState, inputs: Inputs, err: Exception, started: float
    ) -> tuple[EngineState, Snapshot, Effects]:
        """Count the failure, republish, and enter safe mode at three (D7 §2, §8).

        Safe mode releases every load and treats the site as observing: devices
        in their fail-safe states are safer than devices under a controller of
        unknown state (INV-64), and the publish continues (INV-44).
        """
        cfg = inputs.site.engine
        failures = state.runtime.failures + 1
        enter = failures >= cfg.safe_mode_after_failures
        reasons = [
            f"engine failed ({failures} in a row): {type(err).__name__}: {err}",
        ]
        commands: list[LoadCommand] = []
        repairs: list[RepairIssue] = []
        events: list[HaEvent] = []
        notes: list[Notification] = []
        load_states = dict(state.loads)
        if enter and not state.runtime.safe_mode:
            reasons.append("safe mode: releasing every load and switching to observe")
            for load in self._loads:
                ctx = self._ctx(load, inputs, active=False)
                before = load_states.get(load.load_id, LoadState())
                after, result = load.release(before, ctx, reason="safe mode")
                load_states[load.load_id] = after
                command = _command_of(load.load_id, result, after.gate, ctx.reads)
                if command is not None:
                    commands.append(command)
            repairs.append(
                RepairIssue(
                    issue_id="engine_failing",
                    translation_key="engine_failing",
                    params={"failures": failures, "error": f"{type(err).__name__}: {err}"},
                    severe=True,
                )
            )
            events.append(HaEvent(EventKind.SAFE_MODE, {"failures": failures, "error": str(err)}))
            notes.append(
                Notification(
                    category="engine",
                    key="engine_failing",
                    params={"failures": failures},
                    severity="warn",
                )
            )
            _LOGGER.warning("safe mode after %s consecutive engine failures", failures)

        duration_ms = (time.perf_counter() - started) * 1000.0
        runtime = replace(
            state.runtime,
            tick_no=state.runtime.tick_no + 1,
            last_tick_at=inputs.now,
            failures=failures,
            safe_mode=state.runtime.safe_mode or enter,
        )
        snapshot = _failed_snapshot(
            state.last_snapshot,
            inputs,
            runtime,
            duration_ms,
            reasons=_trail(reasons, cfg.max_reasons),
            safe_mode=runtime.safe_mode,
            failures=failures,
        )
        new_state = replace(state, loads=load_states, runtime=runtime, last_snapshot=snapshot)
        return (
            new_state,
            snapshot,
            Effects(
                commands=tuple(commands),
                ha_events=tuple(events),
                store_dirty=frozenset({Section.RUNTIME}),
                store_now=frozenset({Section.LOADS}) if commands else frozenset(),
                notifications=tuple(notes),
                repairs=tuple(repairs),
            ),
        )

    # ------------------------------------------------------------- the pieces #

    def _ctx(
        self,
        load: Load,
        inputs: Inputs,
        *,
        active: bool,
        budget: TransportBudget | None = None,
        stage: int = 0,
        setpoint_delta: float = 0.0,
        desired: Any = None,
    ) -> LoadCtx:
        """Return one load's context for this tick (D4 §5.1)."""
        row = inputs.loads.get(load.load_id)
        return LoadCtx(
            now=inputs.now,
            reads=row.reads if row is not None else Reads(at=inputs.now),
            electrical=inputs.site.electrical,
            budget=inputs.transport if budget is None else budget,
            site_active=active,
            presence=inputs.knobs.presence
            if inputs.knobs.presence is not None
            else LoadCtx(
                now=inputs.now, reads=Reads(at=inputs.now), electrical=inputs.site.electrical
            ).presence,
            setpoint_delta=setpoint_delta,
            desired=desired,
            calendar=row.calendar if row is not None else (),
            outdoor_c=inputs.outdoor_c,
            indoor_c=inputs.indoor_c,
            zone=inputs.site.tz,
        )

    def _mode_edges(
        self, state: EngineState, inputs: Inputs, active: bool, reasons: list[str]
    ) -> tuple[dict[str, LoadState], list[LoadCommand], frozenset[Section]]:
        """Honour the mode knobs and the site switch before anything is decided.

        A mode edge releases or restores at once (D4 §5.2, INV-26, INV-29): a
        load never inherits the mode it was left in, and the site switch turning
        off releases every load on the edge (PLAN §7 dec. 20).
        """
        states = dict(state.loads)
        commands: list[LoadCommand] = []
        dirty: set[Section] = set()
        was_active = state.events.edges.get("site_active")
        site_edge = was_active == "1" and not active
        for load in self._loads:
            before = states.get(load.load_id, LoadState())
            wanted = inputs.knobs.modes.get(load.load_id, before.mode)
            max_h = inputs.knobs.force_max_h.get(load.load_id, before.force_max_h)
            if max_h != before.force_max_h:
                before = replace(before, force_max_h=max_h)
                states[load.load_id] = before
                dirty.add(Section.LOADS)
            if wanted == before.mode and not site_edge:
                continue
            edge = transition(before, wanted, inputs.now) if wanted != before.mode else None
            after = before if edge is None else edge.state
            ctx = self._ctx(load, inputs, active=active)
            if (edge is not None and edge.release) or site_edge:
                after, result = load.release(after, ctx, reason=edge.reason if edge else "site off")
                reasons.append(f"{load.load_id}: released ({result.reason})")
                command = _command_of(load.load_id, result, after.gate, ctx.reads)
                if command is not None:
                    commands.append(command)
            elif edge is not None and edge.restore:
                after, result = load.restore(after, ctx, reason=edge.reason)
                reasons.append(f"{load.load_id}: restored ({result.reason})")
                command = _command_of(load.load_id, result, after.gate, ctx.reads)
                if command is not None:
                    commands.append(command)
            states[load.load_id] = after
            dirty.add(Section.LOADS)
        return states, commands, frozenset(dirty)

    def _views(
        self,
        states: Mapping[str, LoadState],
        inputs: Inputs,
        active: bool,
        failed: dict[str, str],
    ) -> dict[str, ControlledView]:
        """Return each load's meter row, isolated per load (D3 §5.8, INV-45).

        A load whose own view raises is counted as measuring nothing, which puts
        its draw in the uncontrolled term: a load we cannot reason about is a
        load we do not steer, and the reserve is the right place for it.
        """
        views: dict[str, ControlledView] = {}
        for load in self._loads:
            state = states.get(load.load_id, LoadState())
            try:
                views[load.load_id] = load.view_for_meter(
                    state, self._ctx(load, inputs, active=active)
                )
            except Exception as err:
                _LOGGER.exception("load %s failed to report its meter row", load.load_id)
                failed[load.load_id] = f"view: {type(err).__name__}: {err}"
                views[load.load_id] = ControlledView(
                    load_id=load.load_id,
                    measured_w=None,
                    commanded_w=None,
                    settling=False,
                    phases=load.config.phase_names,
                )
        return views

    def _observe(
        self,
        states: Mapping[str, LoadState],
        inputs: Inputs,
        active: bool,
        failed: dict[str, str],
        ladder: LadderState,
    ) -> tuple[dict[str, Any], dict[str, Any], tuple[LoadView, ...]]:
        """Observe every load and project it for the planner, isolated (INV-45)."""
        out_states = dict(states)
        observations: dict[str, Any] = {}
        views: list[LoadView] = []
        for load in self._loads:
            if load.load_id in failed:
                continue
            before = out_states.get(load.load_id, LoadState())
            ctx = self._ctx(load, inputs, active=active, stage=ladder.stage)
            try:
                after, observation = load.observe(before, ctx)
            except Exception as err:
                _LOGGER.exception("load %s failed to observe", load.load_id)
                failed[load.load_id] = f"observe: {type(err).__name__}: {err}"
                continue
            out_states[load.load_id] = after
            observations[load.load_id] = observation
            mode = load.mode_now(after, ctx)
            views.append(
                LoadView.of(
                    load,
                    observation.demand,
                    mode=mode,
                    level_now=_level_now(observation.demand, ctx.reads),
                    quantiser=_quantiser(load, after, ctx, mode),
                )
            )
        return out_states, observations, tuple(views)

    def _apply(  # noqa: PLR0917 - the tick's threaded inputs, positional by design
        self,
        states: Mapping[str, LoadState],
        inputs: Inputs,
        active: bool,
        failed: dict[str, str],
        grants: Grants,
        ladder: LadderState,
        plans: Mapping[str, Plan],
    ) -> tuple[dict[str, LoadState], dict[str, ApplyResult], list[LoadCommand]]:
        """Turn each grant into the fewest, safest writes, isolated (INV-45).

        In priority order, because the transport budget threads from load to load
        and a bucket is a site-level resource (INV-58).
        """
        out_states = dict(states)
        results: dict[str, ApplyResult] = {}
        commands: list[LoadCommand] = []
        budget = inputs.transport
        for load in self._loads:
            grant = grants.get(load.load_id)
            if grant is None or load.load_id in failed:
                continue
            plan = plans.get(load.load_id)
            before = out_states.get(load.load_id, LoadState())
            ctx = self._ctx(
                load,
                inputs,
                active=active,
                budget=budget,
                stage=ladder.stage,
                setpoint_delta=_setpoint_delta(plan, inputs.now),
                desired=_desired(plan, inputs.now),
            )
            try:
                after, result = load.apply(grant, before, ctx)
            except Exception as err:
                _LOGGER.exception("load %s failed to apply its grant", load.load_id)
                failed[load.load_id] = f"apply: {type(err).__name__}: {err}"
                continue
            out_states[load.load_id] = after
            results[load.load_id] = result
            budget = result.budget
            command = _command_of(load.load_id, result, after.gate, ctx.reads)
            if command is not None:
                commands.append(command)
        return out_states, results, commands

    # ------------------------------------------------------------- the tariff #

    def _closed_ceiling_kwh(self, window: Any, knobs: Knobs) -> float:
        """Return the flat ceiling that applied to a window that has closed.

        The PI integrates utilisation against the window's own ceiling (D6 §5.1).
        The flat target for that window is what D2 can still answer exactly -
        the slack and the free ride are properties of the moment, not of a window
        that is over (`design/DECISIONS.md` D-0233).
        """
        target_w = float(self._tariff.target_w_at(window.start_utc, knobs.target))
        if math.isinf(target_w):
            return 0.0
        return float(target_w / 1000.0 * (float(window.window_min) / 60.0))

    def _target_kwh(self, now: datetime, target: Target, window_min: int) -> float | None:
        """Return the flat target of the window in progress, in kWh (D6 §5.4)."""
        target_w = self._tariff.target_w_at(now, target)
        if math.isinf(target_w):
            return None
        return target_w / 1000.0 * (window_min / 60.0)

    def _hard_limits(
        self, now: datetime, meter: MeterSnapshot, runtime: RuntimeState
    ) -> tuple[HardLimits, RuntimeState]:
        """Return the site's instantaneous limits and the trip clock (D2 §5.8)."""
        contracted: HardLimit | None = self._tariff.limit_now_w(now, self.site.electrical)
        total = _smooth_w(meter)
        over_since = runtime.over_since
        if contracted is not None and total > contracted.w:
            over_since = over_since if over_since is not None else now
        else:
            over_since = None
        over_for_s = 0.0 if over_since is None else (now - over_since).total_seconds()
        return (
            HardLimits(
                fuse_w=self.site.electrical.fuse_w(),
                contracted=contracted,
                over_for_s=over_for_s,
            ),
            replace(runtime, over_since=over_since),
        )

    def _tariff_status(
        self, now: datetime, ceiling: Ceiling, budget: Budget, hard: HardLimits
    ) -> TariffStatus:
        """Return the tariff section of the snapshot (D7 §4.1)."""
        period = self._tariff.period(now)
        level = self._tariff.level()
        return TariffStatus(
            version_id=self._tariff.active_version().version_id,
            eligible=ceiling.eligible,
            weight=ceiling.weight,
            ceiling_kwh=ceiling.kwh,
            ceiling_reason=ceiling.reason,
            slack_kwh=ceiling.slack_kwh,
            free_ride=ceiling.free_ride,
            metric_kw=self._tariff.metric(),
            level=level,
            projected_level=self._tariff.projected_level(budget.projected_kwh),
            top=_top_entries(self._tariff, period.key),
            advice=tuple(self._tariff.advice()),
            period_key=period.key,
            period_start=period.start,
            period_end=period.end,
            hard_limit_w=None if hard.contracted is None else hard.contracted.w,
        )

    # ----------------------------------------------------------- the warnings #

    def _warnings(  # noqa: PLR0917 - the tick's threaded inputs, positional by design
        self,
        runtime: RuntimeState,
        inputs: Inputs,
        budget: Budget,
        ceiling: Ceiling,
        meter: MeterSnapshot,
        views: Sequence[LoadView],
        plans: Mapping[str, Plan],
    ) -> tuple[tuple[SiteWarning, ...], RuntimeState, list[HaEvent], list[Notification]]:
        """Return the peak warnings for the coming windows (D7 §5.4, EMA variant).

        The EMA variant: without D10 the expectation for a coming window is the
        current uncontrolled EMA held for the window, plus what the plans intend
        to move in it, plus what an urgent demand will take whether it is planned
        or not. WP5.2 replaces the first term with the baseline.
        """
        cfg = inputs.site.engine
        warnings: list[SiteWarning] = []
        events: list[HaEvent] = []
        notes: list[Notification] = []
        warned = set(runtime.peak.warned)
        ema = runtime.peak.ema_w

        for start, end in _coming_windows(meter, cfg.warn_horizon_h):
            limit = self._window_ceiling_kwh(start, end, inputs.knobs.target)
            if math.isinf(limit) or limit <= 0.0:
                continue
            hours = (end - start).total_seconds() / 3600.0
            drivers: list[tuple[str, float]] = []
            expected = 0.0 if ema is None else ema / 1000.0 * hours
            if expected > 0.0:
                drivers.append(("uncontrolled", expected))
            for view in views:
                planned = _planned_kwh(plans.get(view.load_id), start, end)
                if planned <= 0.0 and _unplanned_want(view, plans.get(view.load_id), start, end):
                    planned = max(0.0, view.max_w) / 1000.0 * hours
                if planned > 0.0:
                    drivers.append((view.load_id, planned))
                    expected += planned
            if expected >= cfg.warn_fraction * limit and start not in warned:
                warned.add(start)
                warning = SiteWarning(
                    kind="peak",
                    key=f"peak:{start.isoformat()}",
                    window_start=start,
                    window_end=end,
                    expected_kwh=expected,
                    ceiling_kwh=limit,
                    drivers=tuple(sorted(drivers, key=lambda row: -row[1])[:3]),
                    advice=("shift what you can out of the window",),
                )
                warnings.append(warning)
                events.append(HaEvent(EventKind.PEAK_WARNING, _warning_data(warning, active=True)))
                notes.append(
                    Notification(
                        category="peak_warning",
                        key=warning.key,
                        params=_warning_data(warning, active=True),
                        severity="warn",
                    )
                )
                _LOGGER.warning(
                    "peak warning for %s: %.2f kWh expected against a %.2f kWh ceiling",
                    start.isoformat(),
                    expected,
                    limit,
                )
            elif expected < cfg.clear_fraction * limit and start in warned:
                warned.discard(start)
                events.append(
                    HaEvent(
                        EventKind.PEAK_WARNING,
                        {
                            "active": False,
                            "window_start": start.isoformat(),
                            "expected_kwh": expected,
                            "ceiling_kwh": limit,
                        },
                    )
                )
            elif start in warned:
                warnings.append(
                    SiteWarning(
                        kind="peak",
                        key=f"peak:{start.isoformat()}",
                        window_start=start,
                        window_end=end,
                        expected_kwh=expected,
                        ceiling_kwh=limit,
                        drivers=tuple(sorted(drivers, key=lambda row: -row[1])[:3]),
                    )
                )

        live = _live_warning(budget, ceiling, meter)
        if live is not None:
            warnings.append(live)
            if live.key != runtime.peak.live:
                # An edge, not a heartbeat: the same live warning is one event and
                # one notification, however many ticks it stays true.
                events.append(HaEvent(EventKind.PEAK_WARNING, _warning_data(live, active=True)))
                notes.append(
                    Notification(
                        category="peak_warning",
                        key=live.key,
                        params=_warning_data(live, active=True),
                        severity="warn",
                    )
                )
        elif runtime.peak.live is not None:
            events.append(
                HaEvent(
                    EventKind.PEAK_WARNING,
                    {
                        "active": False,
                        "key": runtime.peak.live,
                        "window_start": meter.window_start_utc.isoformat(),
                    },
                )
            )

        peak = replace(
            runtime.peak,
            warned=tuple(sorted(w for w in warned if w >= meter.window_start_utc)),
            live=None if live is None else live.key,
        )
        return tuple(warnings), replace(runtime, peak=peak), events, notes

    def _window_ceiling_kwh(self, start: datetime, end: datetime, target: Target) -> float:
        """Return the flat ceiling of a coming window, in kWh (D5 §5.1's rule)."""
        target_w = self._tariff.target_w_at(start, target)
        if math.isinf(target_w):
            return math.inf
        return target_w / 1000.0 * ((end - start).total_seconds() / 3600.0)

    # ------------------------------------------------------------- the loads #

    def _load_statuses(  # noqa: PLR0917 - the tick's threaded inputs, positional by design
        self,
        states: Mapping[str, LoadState],
        inputs: Inputs,
        grants: Grants,
        observations: Mapping[str, Any],
        results: Mapping[str, ApplyResult],
        views: Mapping[str, ControlledView],
        failed: Mapping[str, str],
        frozen: bool,
    ) -> dict[str, LoadStatus]:
        """Return one row per load: what it wanted, got, and did (D7 §4.1)."""
        out: dict[str, LoadStatus] = {}
        for load in self._loads:
            load_id = load.load_id
            state = states.get(load_id, LoadState())
            grant = grants.get(load_id)
            observation = observations.get(load_id)
            result = results.get(load_id)
            demand = (
                observation.demand
                if observation is not None
                else Demand(
                    wants=False,
                    required_kwh=None,
                    deadline=None,
                    min_w=0.0,
                    max_w=0.0,
                    urgency=Urgency.NONE,
                    comfort=None,
                    price_sensitive=False,
                    reason="not observed this tick",
                )
            )
            view = views.get(load_id)
            out[load_id] = LoadStatus(
                load_id=load_id,
                name=load.config.name,
                type_key=load.config.type_key,
                priority=load.config.priority,
                mode=effective_mode(state.mode, inputs.knobs.active),
                configured_mode=state.mode,
                granted_w=0.0 if grant is None else grant.w,
                measured_w=measured_w(view),
                reserved_w=0.0
                if grant is None or load_id not in observations
                else reserved_w(_view_of(load_id, observations, demand, load), grant.w, view),
                commanded_w=state.commanded_w,
                demand=demand,
                comfort=demand.comfort,
                shed=False if grant is None else grant.shed,
                shed_reason=None if grant is None else grant.shed_reason,
                stage=0 if grant is None else grant.stage,
                blunt=False if grant is None else grant.blunt,
                capped_by=() if grant is None else grant.capped_by,
                action=Action.OBSERVE if result is None else result.action,
                action_reason="" if result is None else result.reason,
                health=observation.health
                if observation is not None
                else Health(
                    ok=load_id not in failed,
                    unhealthy=load_id in failed,
                    failures=0,
                    transient_since=None,
                    stale_roles=(),
                    last_error=failed.get(load_id),
                ),
                latches=LoadLatches(
                    shed_active=state.shed_active,
                    shed_since=state.shed_since,
                    session_done=state.session_done is not None,
                    session_done_reason=None
                    if state.session_done is None
                    else state.session_done.reason,
                    force_since=state.force_since,
                    force_max_h=state.force_max_h,
                    provisioned=dict(state.provisioned),
                ),
                learned={key: row.value for key, row in state.learned.items()},
                held=frozen or load_id in failed,
                error=failed.get(load_id),
            )
        return out

    # ------------------------------------------------------------ the planner #

    def plan(  # noqa: PLR0915 - D7 §5.2's cycle, kept in one place and in order
        self, state: EngineState, inputs: Inputs
    ) -> tuple[EngineState, PlanReport, Effects]:
        """Run one planning cycle (D7 §5.2). Fetches nothing: the runtime did the I/O.

        The cycle rebuilds every load's plan, adopts past the hysteresis of
        D5 §5.9, and closes the accounting slots that ended since the last cycle -
        oldest first, exactly once each, and **never** in the tick (INV-68).
        """
        started = time.perf_counter()
        now = inputs.now
        site = inputs.site
        reasons: list[str] = []
        events: list[HaEvent] = []
        notes: list[Notification] = []
        failed: dict[str, str] = {}
        dirty: set[Section] = set()
        edges = dict(state.events.edges)
        active = inputs.knobs.active and not state.runtime.safe_mode

        load_states, _observations, views = self._observe(
            state.loads, inputs, active, failed, state.alloc.ladder
        )
        plans = state.plans
        adopted: tuple[str, ...] = ()
        uncovered: tuple[str, ...] = ()
        if inputs.curves is None:
            reasons.append("no price curve: the adopted plans stand (D1 §8)")
        else:
            eps_base = (
                site.budget.eps_base_kwh
                if inputs.knobs.eps_base_kwh is None
                else inputs.knobs.eps_base_kwh
            )
            site_ctx = SiteContext(
                tz=site.tz,
                hysteresis=site.hysteresis,
                tariff=self._tariff,
                target=inputs.knobs.target,
                presence=inputs.knobs.presence,
                forecasts=inputs.forecasts,
                events=inputs.events,
                horizon_h=site.horizon_h,
                stale=_curves_stale(inputs.curves, now),
                eps_w=eps_for_window(eps_base, site.window_min) * 60.0 / site.window_min,
                plan_fraction=site.ladder.thresholds[1],
            )
            site_plan = plan_all(views, inputs.curves, site_ctx, now, previous=state.plans.plans)
            adopted = tuple(sorted(site_plan.adopted))
            uncovered = site_plan.uncovered
            plans = PlansState(schema=state.plans.schema, plans=dict(site_plan.plans), built_at=now)
            if adopted:
                dirty.add(Section.PLANS)
                reasons.append(f"adopted: {', '.join(adopted)}")
            for load_id in adopted:
                plan = site_plan.plans[load_id]
                events.append(
                    HaEvent(
                        EventKind.PLAN_ADOPTED,
                        {
                            "load": load_id,
                            "strategy": plan.strategy,
                            "planned_kwh": plan.planned_kwh,
                            "cost": str(plan.cost_estimate.amount),
                            "currency": plan.cost_estimate.currency,
                            "covered": plan.covered,
                            "reason": plan.reason,
                        },
                    )
                )
            for load_id in uncovered:
                if edges.get(f"uncovered:{load_id}") == "1":
                    continue
                events.append(
                    HaEvent(
                        EventKind.DEADLINE_AT_RISK,
                        {
                            "load": load_id,
                            "coverage": site_plan.plans[load_id].coverage,
                            "deadline": _iso(site_plan.plans[load_id].deadline),
                        },
                    )
                )
                notes.append(
                    Notification(
                        category="deadline_at_risk",
                        key=f"deadline:{load_id}",
                        params={"load": load_id},
                        severity="warn",
                    )
                )
            for view in views:
                edges[f"uncovered:{view.load_id}"] = "1" if view.load_id in uncovered else "0"

        runtime, closes = self._close_slots(state, inputs)
        accounting = dict(state.accounting)
        month_closed: str | None = None
        for close in closes:
            accounting = dict(close.state) if close.state else accounting
            month_closed = close.month_closed or month_closed
            if close.month_closed is not None:
                events.append(HaEvent(EventKind.MONTH_CLOSED, {"period": close.month_closed}))
        if closes:
            dirty.add(Section.ACCOUNTING)
            reasons.append(
                f"accounting: closed {len(closes)} slot(s) up to {_iso(runtime.closed_to)}"
            )

        duration_ms = (time.perf_counter() - started) * 1000.0
        runtime = replace(runtime, last_plan_at=now)
        dirty.add(Section.RUNTIME)
        if load_states != state.loads:
            dirty.add(Section.LOADS)
        if edges != state.events.edges:
            dirty.add(Section.EVENTS)
        new_state = replace(
            state,
            loads=load_states,
            plans=plans,
            accounting=accounting,
            events=EventsState(schema=state.events.schema, edges=edges),
            runtime=runtime,
        )
        report = PlanReport(
            at=now,
            duration_ms=duration_ms,
            adopted=adopted,
            plans=plans.plans,
            uncovered=uncovered,
            slots_closed=len(closes),
            month_closed=month_closed,
            failed=dict(failed),
            reasons=_trail(reasons, site.engine.max_reasons),
        )
        _LOGGER.debug(
            "plan: %s load(s), %s adopted, %s slot(s) closed, %.1f ms",
            len(views),
            len(adopted),
            len(closes),
            duration_ms,
        )
        return (
            new_state,
            report,
            Effects(
                ha_events=tuple(events),
                store_dirty=frozenset(dirty),
                notifications=tuple(notes),
            ),
        )

    def _close_slots(
        self, state: EngineState, inputs: Inputs
    ) -> tuple[RuntimeState, tuple[AccountingClose, ...]]:
        """Close every price slot that ended since the last cycle, oldest first.

        A cycle skipped for an hour closes the backlog on the next one, in order,
        exactly once each (D7 §9 16). Without a hook - and until WP0.10a wires
        D11's `close_slot` - the cursor still advances, so no slot is closed
        twice when it lands.
        """
        runtime = state.runtime
        if inputs.curves is None:
            return runtime, ()
        curve = inputs.curves.import_.get(Carrier.ELECTRICITY)
        if curve is None:
            return runtime, ()
        cursor = runtime.closed_to
        closed: list[AccountingClose] = []
        last: datetime | None = None
        for slot in curve.slots:
            if slot.end > inputs.now or (cursor is not None and slot.end <= cursor):
                continue
            last = slot.end
            if self._accounting is not None:
                closed.append(self._accounting.close_slot(slot.start, slot.end, now=inputs.now))
        if last is None:
            return runtime, ()
        return (
            replace(
                runtime,
                closed_to=last,
                slots_closed=runtime.slots_closed + max(len(closed), 1 if last else 0),
            ),
            tuple(closed),
        )


# --------------------------------------------------------------------------- #
# Free functions - the arithmetic the tick needs and nothing else
# --------------------------------------------------------------------------- #


def _is_boundary_tick(now: datetime, window_min: int, tz: tzinfo) -> bool:
    """Return whether `now` is exactly a window boundary (INV-43).

    On the ancestor controller a cron at `HH:00:00` ran a full tick ten seconds
    before the register report that closes the window, so the tick saw the
    departing window's energy as the arriving one's and actuated on the old
    boundary. The runtime must never schedule one; the tick says so if it does.
    """
    start, _ = window_bounds(now, window_min, tz)
    return now == start


def _smooth_w(meter: MeterSnapshot) -> float:
    """Return the smoothed total the ladder and the trip clock read (INV-38)."""
    if meter.grid_smooth_w is not None:
        return meter.grid_smooth_w
    return meter.grid_w or 0.0


def _baseline(forecasts: Forecasts | None) -> Baseline | None:
    """Return D10's baseline for the budget - `None` until WP5.2 (D-0161)."""
    return None


def _window_stats(runtime: RuntimeState, meter: MeterSnapshot) -> RuntimeState:
    """Fold this tick's uncontrolled power into the window's statistics (D6 §5.1)."""
    if runtime.window_start != meter.window_start_utc:
        runtime = replace(
            runtime,
            window_start=meter.window_start_utc,
            uc_peak_w=0.0,
            uc_mean_w=0.0,
            uc_samples=0,
        )
    value = meter.uncontrolled_w
    if value is None:
        return runtime
    samples = runtime.uc_samples + 1
    return replace(
        runtime,
        uc_peak_w=max(runtime.uc_peak_w, value),
        uc_mean_w=runtime.uc_mean_w + (value - runtime.uc_mean_w) / samples,
        uc_samples=samples,
    )


def _ema(runtime: RuntimeState, meter: MeterSnapshot, frozen: bool, cfg: EngineCfg) -> RuntimeState:
    """Fold uncontrolled power into the peak warning's EMA (§5.4).

    Frozen ticks do not move it: an average fed by blindness is worse than an
    average that stands still (INV-15).
    """
    value = meter.uncontrolled_w
    if frozen or value is None:
        return runtime
    peak = runtime.peak
    at = meter.now
    if peak.ema_w is None or peak.ema_at is None:
        return replace(runtime, peak=replace(peak, ema_w=value, ema_at=at))
    dt = (at - peak.ema_at).total_seconds()
    if dt <= 0.0:
        return runtime
    if dt > _EMA_RESET_S:
        return replace(runtime, peak=replace(peak, ema_w=value, ema_at=at))
    alpha = 1.0 - math.exp(-dt / cfg.ema_tau_s)
    return replace(
        runtime, peak=replace(peak, ema_w=peak.ema_w + alpha * (value - peak.ema_w), ema_at=at)
    )


def _coming_windows(
    meter: MeterSnapshot, horizon_h: float
) -> tuple[tuple[datetime, datetime], ...]:
    """Return the windows starting inside the warning horizon (§5.4)."""
    step = timedelta(minutes=meter.window_min)
    out: list[tuple[datetime, datetime]] = []
    start = meter.window_start_utc + step
    end = meter.window_start_utc + timedelta(hours=horizon_h)
    while start < end:
        out.append((start, start + step))
        start += step
    return tuple(out)


def _planned_kwh(plan: Plan | None, start: datetime, end: datetime) -> float:
    """Return what a plan intends to move inside `[start, end)` (D5 §4)."""
    if plan is None:
        return 0.0
    total = 0.0
    for slot in plan.slots:
        overlap = (min(slot.end, end) - max(slot.start, start)).total_seconds()
        if overlap <= 0.0 or slot.hours <= 0.0:
            continue
        total += slot.kwh * (overlap / (slot.hours * 3600.0))
    return total


def _unplanned_want(view: LoadView, plan: Plan | None, start: datetime, end: datetime) -> bool:
    """Return whether this load will take power in a window nothing planned.

    The four cases with no vote - a force, a min-SoC floor, a legionella cycle, a
    comfort violation - take their power whatever the price says, so the warning
    has to count them (§5.4's "reserved unplanned wants").
    """
    if not view.demand.wants or view.demand.price_sensitive:
        return False
    return plan is None or plan.mode is PlanMode.NONE


#: A live over-projection is worth a warning only when the household itself is
#: the driver: below this share of the total the controller's own loads are.
_LIVE_UNCONTROLLED_SHARE: Final = 0.6


def _live_warning(budget: Budget, ceiling: Ceiling, meter: MeterSnapshot) -> SiteWarning | None:
    """Return the live warning: the window is going over and it is not us (§5.4)."""
    if not ceiling.eligible or budget.projected_kwh <= budget.ceiling_kwh:
        return None
    total = abs(_smooth_w(meter))
    uncontrolled = abs(meter.uncontrolled_w or 0.0)
    if total <= 0.0 or uncontrolled / total <= _LIVE_UNCONTROLLED_SHARE:
        return None
    return SiteWarning(
        kind="peak_uncontrolled",
        key=f"peak_uncontrolled:{meter.window_start_utc.isoformat()}",
        window_start=meter.window_start_utc,
        window_end=meter.window_start_utc + timedelta(minutes=meter.window_min),
        expected_kwh=budget.projected_kwh,
        ceiling_kwh=budget.ceiling_kwh,
        drivers=(("uncontrolled", uncontrolled / 1000.0 * meter.t_rem_h),),
        advice=("something not controlled by powerplan is running — oven or sauna?",),
    )


def _warning_data(warning: SiteWarning, *, active: bool) -> dict[str, Any]:
    """Return one warning as an event payload (D8 §5.6)."""
    return {
        "active": active,
        "kind": warning.kind,
        "window_start": _iso(warning.window_start),
        "window_end": _iso(warning.window_end),
        "expected_kwh": warning.expected_kwh,
        "ceiling_kwh": warning.ceiling_kwh,
        "drivers": [[name, kwh] for name, kwh in warning.drivers],
        "advice": list(warning.advice),
    }


def _level_notification(
    edges: dict[str, str], tariff: TariffModel, budget: Budget
) -> list[Notification]:
    """Return the "level about to step up" notification, once per edge (D8 §6.8)."""
    level = tariff.level()
    projected = tariff.projected_level(budget.projected_kwh)
    rising = (
        level.index is not None and projected.index is not None and projected.index > level.index
    )
    key = f"{projected.name}" if rising else ""
    if edges.get("level_step", "") == key:
        return []
    edges["level_step"] = key
    if not rising:
        return []
    return [
        Notification(
            category="level_step",
            key=f"level_step:{projected.name}",
            params={"from": level.name, "to": projected.name},
            severity="warn",
        )
    ]


def _domain_events(
    edges: dict[str, str],
    ladder: LadderState,
    report: AllocReport,
    observations: Mapping[str, Any],
    failed: Mapping[str, str],
) -> list[HaEvent]:
    """Return the edge-triggered events of this tick (D7 §5.1 step 11)."""
    events: list[HaEvent] = []
    stage_key = f"{ladder.stage}:{reason_key(ladder.reason)}"
    if edges.get("stage") != stage_key:
        edges["stage"] = stage_key
        events.append(
            HaEvent(
                EventKind.STAGE_CHANGED,
                {"stage": ladder.stage, "reason": ladder.reason, "blunt": ladder.blunt},
            )
        )
    breach = "1" if report.over_allowance else "0"
    if edges.get("breach") != breach:
        edges["breach"] = breach
        if report.over_allowance:
            events.append(
                HaEvent(
                    EventKind.BREACH,
                    {
                        "breach_w": report.breach_w,
                        "deficit_w": report.deficit_w,
                        "reserved": [
                            [row.load, row.granted_w, row.measured_w, row.reserved_w]
                            for row in report.reserved
                        ],
                    },
                )
            )
    comfort = ",".join(sorted(report.comfort))
    if edges.get("comfort") != comfort:
        edges["comfort"] = comfort
        if report.comfort:
            events.append(
                HaEvent(
                    EventKind.COMFORT_VIOLATION,
                    {"loads": list(report.comfort), "breach_w": report.breach_w},
                )
            )
    for load_id, observation in sorted(observations.items()):
        unhealthy = "1" if observation.health.unhealthy else "0"
        if edges.get(f"unhealthy:{load_id}") == unhealthy:
            continue
        edges[f"unhealthy:{load_id}"] = unhealthy
        if observation.health.unhealthy:
            events.append(
                HaEvent(
                    EventKind.DEVICE_UNHEALTHY,
                    {"load": load_id, "error": observation.health.last_error},
                )
            )
    for load_id, error in sorted(failed.items()):
        if edges.get(f"unhealthy:{load_id}") == "1":
            continue
        edges[f"unhealthy:{load_id}"] = "1"
        events.append(HaEvent(EventKind.DEVICE_UNHEALTHY, {"load": load_id, "error": error}))
    return events


def _grant_reasons(views: Sequence[LoadView], grants: Grants) -> list[str]:
    """Return one line per load: what it got and why (HLD §7.4)."""
    lines: list[str] = []
    for view in views:
        grant = grants.get(view.load_id)
        if grant is None:
            continue
        shed = f", shed: {grant.shed_reason}" if grant.shed else ""
        capped = f", capped by {'/'.join(grant.capped_by)}" if grant.capped_by else ""
        lines.append(f"{view.load_id}: {grant.w:.0f} W at stage {grant.stage}{capped}{shed}")
    return lines


def _command_of(
    load_id: str, result: ApplyResult, gate: GateState, reads: Reads
) -> LoadCommand | None:
    """Return the executor's work for one load, or `None` when there is none.

    Only a written or observed decision reaches `writegate.py`: a held or
    unchanged one has nothing to send, and the gate state is already threaded
    into the `LoadState` the engine returns. `current` is read here, because the
    INFO line the executor logs is "old → new, why" and only the engine holds
    the reads (D4 §5.10).
    """
    if result.action is not Action.WRITTEN and result.action is not Action.OBSERVE:
        return None
    role = None if result.command is None else result.command.role
    return LoadCommand(
        load_id=load_id,
        decision=Decision(
            action=result.action,
            command=result.command,
            value=result.value,
            current=None if role is None else reads.current_of(role),
            reason=result.reason,
            gate=gate,
            budget=result.budget,
            blocking=result.blocking,
            verify_at=result.verify_at,
        ),
    )


def _level_now(demand: Demand, reads: Reads) -> float | None:
    """Return the store's level: the comfort variable, or a state of charge.

    A thermal load's level is the temperature its comfort is measured on and an
    EV's is its SoC; both are what D5's `LoadView.level_now` means, and only the
    engine sees the reads (`design/DECISIONS.md` D-0234).
    """
    if demand.comfort is not None and demand.comfort.current is not None:
        return demand.comfort.current
    return reads.value(Role.SOC)


def _quantiser(load: Load, state: LoadState, ctx: LoadCtx, mode: Mode) -> Any:
    """Return the load's own `quantise`, through its real control kind (D6 §5.3).

    The allocator is charged what the device will actually draw - the 6 A cliff
    when a stop is vetoed - and only the engine can build the `KindCtx` the kind
    needs (D5 §4's `Quantiser`, `design/DECISIONS.md` D-0160).
    """
    base = load.device_type.kind_ctx(load, state, ctx, grant=None, mode=mode)

    def quantise(w: float, *, stop_ok: bool, session_active: bool) -> float:
        kind_ctx = replace(base, stop_ok=stop_ok, session_active=session_active)
        quantised = load.kind.quantise(w, kind_ctx)
        if quantised.effective_w is not None:
            return quantised.effective_w
        if quantised.hold or quantised.value is None:
            return 0.0
        # A temperature was commanded, not watts (D4 §4.2): the element draws its
        # nameplate while it heats, so that is what the allocator is charged when
        # the grant covers it or a comfort violation overrides the ceiling; below
        # the nameplate a thermostat cannot run at all (D-0250).
        on_w = base.on_at_w if base.on_at_w is not None else load.config.nameplate_w
        if kind_ctx.comfort_violated or w + _EPS_W >= on_w:
            return on_w
        return 0.0

    return quantise


def _view_of(load_id: str, observations: Mapping[str, Any], demand: Demand, load: Load) -> LoadView:
    """Return the `LoadView` the reservation table needs for one load (D6 §5.2)."""
    return LoadView.of(load, demand, mode=Mode.AUTO)


def _setpoint_delta(plan: Plan | None, now: datetime) -> float:
    """Return the plan's setpoint delta for this slot, 0 with no plan (D5 §2)."""
    if plan is None:
        return 0.0
    desired = plan.desired_state_at(now)
    return float(desired) if isinstance(desired, float | int) else 0.0


def _desired(plan: Plan | None, now: datetime) -> Any:
    """Return the plan's desired option for this slot, `None` with no plan (D5 §2)."""
    if plan is None:
        return None
    desired = plan.desired_state_at(now)
    return None if isinstance(desired, float | int) else desired


def _plan_statuses(plans: Mapping[str, Plan], now: datetime) -> dict[str, PlanStatus]:
    """Return one row per adopted plan (D7 §4.1)."""
    return {
        load_id: PlanStatus(
            load_id=load_id,
            strategy=plan.strategy,
            mode=plan.mode,
            cap_now_w=plan.cap_w(now),
            next_start=plan.next_active(now),
            planned_kwh=plan.planned_kwh,
            required_kwh=plan.required_kwh,
            cost=plan.cost_estimate,
            covered=plan.covered,
            coverage=plan.coverage,
            deadline=plan.deadline,
            confidence=plan.confidence,
            built_at=plan.built_at,
            reason=plan.reason,
        )
        for load_id, plan in sorted(plans.items())
    }


def _price_status(inputs: Inputs, site: SiteConfig) -> PriceStatus:
    """Return the price section of the snapshot (D7 §4.1, D1 §4)."""
    if inputs.curves is None:
        return PriceStatus()
    now = inputs.now
    day = now.astimezone(site.tz).date()
    carriers: dict[Carrier, CarrierPrices] = {}
    built_at: datetime | None = None
    sources: tuple[str, ...] = ()
    tomorrow = False
    for carrier, curve in inputs.curves.import_.items():
        slot = curve.price_at(now)
        following = curve.price_at(now + timedelta(minutes=slot.minutes)) if slot else None
        totals = [row.total for row in curve.slots_between(*_local_day(day, site.tz))]
        carriers[carrier] = CarrierPrices(
            carrier=carrier,
            currency=curve.currency,
            now=None if slot is None else slot.total,
            next=None if following is None else following.total,
            min_today=min(totals) if totals else None,
            max_today=max(totals) if totals else None,
            mean_today=curve.mean(day, site.tz) if totals else None,
            spread_today=curve.spread(day, site.tz),
            confidence=None if slot is None else slot.confidence,
            coverage_h=curve.coverage_h(now),
        )
        built_at = curve.built_at if built_at is None else max(built_at, curve.built_at)
        sources = tuple(sorted(set(sources) | set(curve.sources)))
        tomorrow = tomorrow or _has_tomorrow(curve, day, site.tz)
    return PriceStatus(
        carriers=carriers,
        tomorrow_available=tomorrow,
        built_at=built_at,
        sources=sources,
        stale=_curves_stale(inputs.curves, now),
    )


def _local_day(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Return the UTC instants of local midnight and the next local midnight."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    return start, start + timedelta(days=1)


def _has_tomorrow(curve: PriceCurve, day: date, tz: tzinfo) -> bool:
    """Return whether the curve carries known prices past the next local midnight."""
    _, midnight = _local_day(day, tz)
    return any(
        slot.confidence is Confidence.KNOWN and slot.start >= midnight for slot in curve.slots
    )


def _curves_stale(curves: Curves | None, now: datetime) -> bool:
    """Return whether the curve in force is anything other than known (D1 §5.7)."""
    if curves is None:
        return True
    for curve in curves.import_.values():
        slot = curve.price_at(now)
        if slot is not None and slot.confidence is not Confidence.KNOWN:
            return True
    return False


def _forecast_status(inputs: Inputs) -> ForecastStatus:
    """Return what D10 could answer this tick (D7 §4.1; WP5.1 fills the rest)."""
    forecasts = inputs.forecasts
    if forecasts is None:
        return ForecastStatus()
    return ForecastStatus(
        available=True,
        outdoor_c=forecasts.outdoor_c(inputs.now),
        surplus_w=forecasts.surplus_w(inputs.now),
        baseline_w=forecasts.baseline_w(inputs.now),
    )


def _accounting_status(state: EngineState) -> AccountingStatus:
    """Return the ledger's section as of the last closed slot (D11, INV-68)."""
    return AccountingStatus(
        slots_closed=state.runtime.slots_closed,
        closed_to=state.runtime.closed_to,
        month_key=state.accounting.get("month_key"),
        per_load=dict(state.accounting.get("per_load", {})),
    )


def _top_entries(tariff: TariffModel, period_key: str) -> tuple[PeakEntry, ...]:
    """Return the period's highest days, highest first (D2 §4)."""
    history = getattr(tariff, "history", None)
    if history is None:
        return ()
    days = history.days_in(period_key)
    rows = sorted(days.items(), key=lambda row: -row[1].max_weighted_kw)[:5]
    return tuple(
        PeakEntry(day=day, kw=rec.max_weighted_kw, estimated=rec.estimated) for day, rec in rows
    )


def _bumped(counters: Mapping[str, int], failed: Mapping[str, str]) -> dict[str, int]:
    """Return the per-load failure counters after this tick (D7 §2)."""
    out = {load_id: count for load_id, count in counters.items() if load_id in failed}
    for load_id in failed:
        out[load_id] = out.get(load_id, 0) + 1
    return out


def _dirty(before: EngineState, after: EngineState) -> frozenset[Section]:
    """Return the sections this tick changed (D7 §7)."""
    dirty: set[Section] = {Section.METER, Section.RUNTIME}
    if before.tariff != after.tariff:
        dirty.add(Section.TARIFF)
    if before.loads != after.loads:
        dirty.add(Section.LOADS)
    if before.alloc != after.alloc:
        dirty.add(Section.ALLOC)
    if before.events != after.events:
        dirty.add(Section.EVENTS)
    return frozenset(dirty)


def _at_once(before: EngineState, after: EngineState, meter: MeterSnapshot) -> frozenset[Section]:
    """Return the sections that must be written now, not in five seconds (INV-14).

    The anchor is what makes a restart exact and it changes once per window, so
    it is written the moment it moves; the integral rides the throttle.
    """
    moved = (
        before.meter is None
        or after.meter is None
        or before.meter.window_start_utc != after.meter.window_start_utc
        or before.meter.anchor_kwh != after.meter.anchor_kwh
        or before.meter.anchor_kind != after.meter.anchor_kind
    )
    if moved or meter.closed:
        return frozenset({Section.METER, Section.TARIFF})
    return frozenset()


def _trail(reasons: Sequence[str], limit: int) -> tuple[str, ...]:
    """Return at most `limit` lines of trail, saying how many were dropped (§4.1)."""
    if len(reasons) <= limit:
        return tuple(reasons)
    return (*reasons[: limit - 1], f"… and {len(reasons) - limit + 1} more")


def _iso(value: datetime | None) -> str | None:
    """Return an ISO-8601 string, or `None`."""
    return None if value is None else value.isoformat()


def _failed_snapshot(
    previous: Snapshot | None,
    inputs: Inputs,
    runtime: RuntimeState,
    duration_ms: float,
    *,
    reasons: tuple[str, ...],
    safe_mode: bool,
    failures: int,
) -> Snapshot:
    """Return what a failing tick publishes (D7 §8, INV-44).

    The previous snapshot, restamped, with the engine's health told plainly. A
    first tick that threw has no previous one, so the site section is all there
    is to say - and saying it is still better than saying nothing.
    """
    health = EngineHealth.SAFE_MODE if safe_mode else EngineHealth.FAILING
    if previous is not None:
        return replace(
            previous,
            at=inputs.now,
            tick_no=runtime.tick_no,
            duration_ms=duration_ms,
            site=replace(previous.site, safe_mode=safe_mode, active=False),
            reasons=reasons,
            health=replace(previous.health, engine=health, failures=failures, tick_ms=duration_ms),
            warnings=previous.warnings,
        )
    site = inputs.site
    return Snapshot(
        schema=SnapshotSchema,
        at=inputs.now,
        tick_no=runtime.tick_no,
        duration_ms=duration_ms,
        site=SiteStatus(
            site_id=site.site_id,
            name=site.name,
            path=site.path,
            active=False,
            safe_mode=safe_mode,
            presence=inputs.knobs.presence,
            target_kw=inputs.knobs.target.kw,
            risk=0.0 if inputs.knobs.risk is None else inputs.knobs.risk,
            trigger=inputs.trigger,
            window_min=site.window_min,
        ),
        meter=None,
        budget=None,
        ladder=LadderState(),
        tariff=None,
        prices=PriceStatus(),
        plans={},
        loads={},
        alloc=AllocReport(p_free_w=0.0),
        forecasts=ForecastStatus(),
        accounting=AccountingStatus(),
        warnings=(),
        reasons=reasons,
        health=HealthStatus(
            engine=health,
            failures=failures,
            unhealthy_loads=(),
            failed_loads={},
            frozen_reason="engine failed before it could sample",
            stale_meter=True,
            degraded_meter=True,
            prices_stale=True,
            forecasts_available=False,
            tick_ms=duration_ms,
            over_budget=False,
            boundary_tick=False,
        ),
    )


# --------------------------------------------------------------------------- #
# The state codec (D7 §7) - one document, sections of primitives
# --------------------------------------------------------------------------- #


#: A `Mapping[K, V]` annotation carries exactly two type arguments.
_KEY_VALUE: Final = 2

#: Below this many watts a grant is nothing (D6 §5.3).
_EPS_W: Final = 1e-6


def _encode(value: Any) -> Any:  # noqa: PLR0911 - one branch per JSON-able shape (D7 §7)
    """Return `value` as JSON-able data (D7 §7).

    Frozen dataclasses of primitives, ISO-8601 datetimes, `StrEnum`s, `Decimal`s
    and tuples of those - which is what every domain promised its state would be
    (D2 §7, D3 §4, D4 §7, D6 §7). A type that carries its own `as_dict` owns its
    shape and is asked for it.
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict) and is_dataclass(value):
        return as_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {_encode(key): _encode(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [_encode(item) for item in value]
    raise TypeError(f"{type(value).__name__} is not persistable state (D7 §7)")


def _decode(kind: Any, raw: Any) -> Any:  # noqa: PLR0911, PLR0912 - one branch per JSON-able shape (D7 §7)
    """Return the value `_encode` wrote, rebuilt as `kind` (D7 §7)."""
    kind = _unalias(kind)
    if raw is None or kind is Any:
        return raw
    origin = get_origin(kind)
    if origin in (UnionType, Union):
        return _decode_union(kind, raw)
    if origin is Literal:
        return raw
    if origin is tuple:
        args = get_args(kind)
        if len(args) == _KEY_VALUE and args[1] is Ellipsis:
            return tuple(_decode(args[0], item) for item in raw)
        return tuple(_decode(arg, item) for arg, item in zip(args, raw, strict=True))
    if origin in (list, set, frozenset):
        (arg,) = get_args(kind) or (Any,)
        return (origin or list)(_decode(arg, item) for item in raw)
    if origin is not None and issubclass(_as_type(origin), Mapping):
        key_kind, value_kind = get_args(kind) or (Any, Any)
        return {_decode(key_kind, key): _decode(value_kind, item) for key, item in raw.items()}
    if not isinstance(kind, type):
        return raw
    if issubclass(kind, Enum):
        return kind(raw)
    if issubclass(kind, datetime):
        return datetime.fromisoformat(raw)
    if issubclass(kind, date):
        return date.fromisoformat(raw)
    if issubclass(kind, Decimal):
        return Decimal(raw)
    if is_dataclass(kind):
        from_dict = getattr(kind, "from_dict", None)
        if callable(from_dict):
            return from_dict(raw)
        hints = get_type_hints(kind)
        return kind(
            **{
                f.name: _decode(hints[f.name], raw[f.name])
                for f in fields(kind)
                if f.init and f.name in raw
            }
        )
    if issubclass(kind, bool | int | float | str):
        return kind(raw)
    return raw


def _decode_union(kind: Any, raw: Any) -> Any:
    """Return `raw` decoded as the member of a union its JSON shape fits (D7 §7)."""
    args = [arg for arg in get_args(kind) if arg is not type(None)]
    if len(args) == 1:
        return _decode(args[0], raw)
    for arg in args:
        candidate = _unalias(arg)
        if not isinstance(candidate, type):
            continue
        if isinstance(raw, dict) and is_dataclass(candidate):
            return _decode(candidate, raw)
        if isinstance(raw, str) and issubclass(candidate, Enum):
            try:
                return candidate(raw)
            except ValueError:
                continue
        if isinstance(raw, bool) and candidate is bool:
            return raw
        if isinstance(raw, int | float) and candidate in (int, float) and not isinstance(raw, bool):
            return candidate(raw)
    return raw


def _unalias(kind: Any) -> Any:
    """Return what a PEP 695 `type X = …` alias stands for."""
    value = getattr(kind, "__value__", None)
    return kind if value is None else _unalias(value)


def _as_type(origin: Any) -> type:
    """Return `origin` as a class, for the `issubclass` questions above."""
    return origin if isinstance(origin, type) else type(origin)
