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

import functools
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum
from typing import (
    Any,
    Final,
    Literal,
    Protocol,
)

from .allocation import (
    BASELINE_CONFIDENCE,
    AllocCfg,
    AllocCtx,
    AllocReport,
    AllocState,
    Baseline,
    Budget,
    BudgetCfg,
    Constraint,
    ContractedPowerLimit,
    CycleReservation,
    ExternalLimit,
    Grants,
    HardLimits,
    Ladder,
    LadderCfg,
    LadderState,
    PhaseLimit,
    SiteFuse,
    Zone,
    ZoneSpec,
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
    KindCtx,
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
from .loads.targets import CalendarEvent, PresenceMode, profile_from_params
from .metering import (
    ClosedWindow,
    ControlledView,
    ElectricalProfile,
    LoadMeter,
    LoadMeterConfig,
    LoadMeterState,
    MeterSample,
    MeterSnapshot,
    WindowMeter,
    WindowState,
    is_fresh,
    window_bounds,
)
from .metering.health import STALE_CAP_S
from .metering.loads import LoadSlot
from .model import (
    Carrier,
    Confidence,
    Grant,
    Money,
    Plan,
    PlanMode,
    PriceCurve,
    Slot,
    Snapshot,
)
from .pricing import Event, HysteresisPolicy
from .pricing import EventKind as PricingEventKind
from .state_codec import decode as _decode
from .state_codec import encode as _encode
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
    "ForecastClose",
    "ForecastHook",
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
SnapshotSchema: int = 5

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
    PRICES_RECEIVED = "prices_received"
    LEVEL_CHANGED = "level_changed"
    PERIOD_CLOSED = "period_closed"
    LEGIONELLA = "legionella"
    CYCLE = "cycle"
    FORCE = "force"
    PRESENCE_CHANGED = "presence_changed"
    EV_CONNECTED = "ev_connected"
    BASELINE_READY = "baseline_ready"


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


#: The knob keys that rebuild a thermal load's target profile (D4 §4.4).
_TARGET_KEYS: Final = ("comfort_c", "floor_c", "max_c", "vacation_c", "follow_presence")

#: D6 §2's evaluation order: outermost physical limit first, preferences last.
_SCOPE_ORDER: Final = ("site", "circuit", "phase", "group", "zone", "load")


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
    #: Per-load parameters a knob moved live - a comfort target, a target SoC, a
    #: one-off deadline (D8 §5.5): merged over the subentry's parameters
    #: for this tick, and the target profile rebuilt when a comfort key is among
    #: them. Never persisted here; the entity restores it (INV-47, D-0282).
    load_params: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


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
    #: Each sub-metered circuit's own meter this tick, by circuit key (D3 §2: a
    #: plain second `MeterSource`, power only, no `WindowMeter` over it). A
    #: circuit without a sub-meter has no entry and is summed from its members
    #: (D6 §5.8).
    circuits: Mapping[str, MeterSample] = field(default_factory=dict)
    #: D10's own confidence at `now` and whether it clears the offer gate
    #: - computed by `runtime.py` from the full `core/forecasts` model
    #: it holds, never by the engine (`forecasts` above is D5's narrow view,
    #: which collapses "not offered" into `0.0` and cannot answer this).
    #: `_forecast_status` only republishes both; INV-2's one-way direction is
    #: why the numbers cross here instead of the engine importing D10.
    forecast_confidence: float | None = None
    forecast_ready: bool = False
    #: D6's own view of D10 for the budget's reserve/projection (D-0319) -
    #: `core/forecasts/model.py::BudgetForecast`, built fresh each tick by
    #: `runtime.py`. `None` with no baseline at all; below `BASELINE_CONFIDENCE`
    #: `budget()` reads its `.confidence` and falls back to `smooth` itself.
    forecast_baseline: Baseline | None = None


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
    #: D3 `LoadMeter.lifetime_kwh`: powerplan's own monotone counter since the
    #: load was added, never the device's register (D8 §5.5 `sensor.<load>_energy`).
    lifetime_kwh: float = 0.0
    #: D3 `LoadEnergySource.value` behind the last accrual: register / power /
    #: estimated (D8 §5.5 `sensor.<load>_energy`'s `source` attr); `None` before
    #: the meter has anything to report.
    energy_source: str | None = None
    #: D6 §5.6's rotation clock (`AllocState.starved_since`) as of this tick, in
    #: seconds - 0 while this member has its turn or is not in a group; how long
    #: it has been held back otherwise (D8 §5.5 `sensor.<load>_starved_s`).
    starved_s: float = 0.0
    #: D4 §5.12's anti-legionella cycle, `None` for a type with none or one
    #: running its own programme (D8 §5.5 `sensor.<load>_next_legionella`).
    legionella_due_at: datetime | None = None


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
    #: The open month's start - D8 §5.5's monetary sensors' `last_reset` (D11
    #: `Ledger.month_start_utc`; not `closed_to`, which is a slot boundary).
    month_start: datetime | None = None
    #: Since when the site's lifetime totals have accrued (D11 `Lifetime.since`) -
    #: D8 §5.5's `since_install` attr, on the site's monetary sensors and, for
    #: want of a per-load one, a load's too.
    since: datetime | None = None
    cost: Money | None = None
    savings: Money | None = None
    #: The site's *savings* confidence (D11 `SavingsConfidence`) - the field the
    #: `month_closed` event already reads. `pricing_confidence` below is the
    #: *slot-pricing* one (D11 `SlotConfidence`, D8 §5.5's `sensor.<site>_cost`
    #: `confidence` attr); the two answer different questions and are not the
    #: same string.
    confidence: str = "none"
    #: WP2.7 - the rest of D11's `SiteFigures`, for `sensor.<site>_cost`/`_savings`.
    pricing_confidence: str = "none"
    energy_cost: Money | None = None
    export_credit: Money | None = None
    capacity_fee: Money | None = None
    energy_savings: Money | None = None
    capacity_savings: Money | None = None
    cf_cost: Money | None = None
    estimated_share: float = 0.0
    previous_cost: Money | None = None
    previous_savings: Money | None = None
    lifetime_cost: Money | None = None
    lifetime_savings: Money | None = None
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
    #: D8's notification policy: when each key was last sent, ISO instants (D8 §7).
    #: The engine carries it and never reads it.
    last_sent: Mapping[str, str] = field(default_factory=dict)


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
    #: Windows D3 closed that the planning loop has not yet handed to D11 (§5.2):
    #: the counterfactual window is recorded when the price slot that ends it is.
    windows_pending: tuple[ClosedWindow, ...] = ()
    #: The end of the last closed window handed to the accounting (D7 §5.2).
    windows_closed_to: datetime | None = None
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
    #: D3 §5.12's per-load slot integrals, and the site's own under `SITE_IMPORT`
    #: / `SITE_EXPORT`; stored in the `meter` section beside the window (D7 §4.2).
    load_meters: Mapping[str, LoadMeterState] = field(default_factory=dict)
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
            Section.METER.value: {
                "window": _encode(self.meter),
                "loads": _encode(self.load_meters),
            },
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

        meter_section = section(Section.METER)
        if isinstance(meter_section, Mapping) and "window" in meter_section:
            window_raw: Any = meter_section.get("window") or None
            loads_raw: Any = meter_section.get("loads") or {}
        else:  # WP0.8 wrote the window state as the whole section
            window_raw, loads_raw = meter_section, {}
        return cls(
            meter=_decode(WindowState, window_raw),
            load_meters=_decode(Mapping[str, LoadMeterState], loads_raw) or {},
            tariff=_decode(TariffState, section(Section.TARIFF)),
            prices=dict(data.get(Section.PRICES.value) or {}),
            plans=_decode(PlansState, section(Section.PLANS)) or PlansState(),
            loads=_decode(Mapping[str, LoadState], _load_rows(data.get(Section.LOADS.value))) or {},
            alloc=_decode(AllocState, section(Section.ALLOC)) or AllocState(),
            forecasts=dict(data.get(Section.FORECASTS.value) or {}),
            accounting=dict(data.get(Section.ACCOUNTING.value) or {}),
            events=_decode(EventsState, section(Section.EVENTS)) or EventsState(),
            runtime=_decode(RuntimeState, section(Section.RUNTIME)) or RuntimeState(),
        )


def _load_rows(section: Any) -> Mapping[str, Any]:
    """Return the `loads` section's rows by load id, without the store's `schema` stamp.

    The store stamps every section it writes with its schema (D7 §2);
    in a section keyed by load id the stamp is not a load, and decoding it as
    one warned on every start (H.1 F-16).
    """
    rows: Mapping[str, Any] = section or {}
    return {key: row for key, row in rows.items() if key != "schema"}


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


@dataclass(frozen=True, slots=True)
class SlotLoad:
    """One load as the ledger needs it at a slot close (D11 §4, the dynamic half of `ShadowCtx`).

    `slot` is D3's energy for the load over the slot, `None` when its meter had
    not closed one (a load added mid-slot); the rest is what the shadow steps
    against - the effective mode, the demand and the measured level.
    """

    load_id: str
    mode: Mode
    demand: Demand | None
    level_now: float | None
    slot: LoadSlot | None


@dataclass(frozen=True, slots=True)
class SlotClose:
    """Everything D11 needs to close one price slot (D7 §5.2, D11 §5.1; D-0267).

    The engine assembles it from D3's load meters, the site's own slot integral,
    the curves in force and the loads' views; the adapter in
    `core/accounting_hook.py` turns it into `ClosedSlot` and `CloseCtx`.
    `presence` is the household's mode in force, which is what a shadow's target
    profile is read under (D11 §5.3: the intent is honoured, the plan is not).
    """

    start: datetime
    end: datetime
    now: datetime
    curves: Curves
    site_import_kwh: float
    site_export_kwh: float
    site_confidence: str
    outdoor_c: float | None
    loads: Mapping[str, SlotLoad]
    window_closed: ClosedWindow | None = None
    presence: PresenceMode = PresenceMode.HOME


class AccountingHook(Protocol):
    """The one call `plan()` makes into D11 (INV-68, D7 §9 16).

    D11's `core/accounting/close.py::close_slot(ClosedSlot, CloseCtx)` is adapted
    to this by `core/accounting/hook.py`, which is what keeps the engine
    from importing `core/accounting` at all: the tick must not be able to reach
    the ledger even by accident, and a `Protocol` taken as a constructor
    argument cannot be reached from `tick()`.
    """

    def close_slot(self, close: SlotClose) -> AccountingClose:
        """Close the price slot `close` describes and return what it changed."""
        ...


class ForecastHook(Protocol):
    """The one call `plan()` makes into D10 (mirrors `AccountingHook`).

    `close_slot` is called for every price slot the same way `AccountingHook`'s
    is - the baseline updates once per *closed window*, not per slot, so the
    adapter (`core/forecasts_hook.py`) is the one that watches for
    `close.window_closed`; the engine itself stays ignorant of the difference
    and, exactly as for accounting, never imports `core/forecasts` (INV-2's
    one-way direction) and never reaches this from `tick()`.
    """

    def close_slot(self, close: SlotClose) -> ForecastClose:
        """Close the price slot `close` describes and return what it changed."""
        ...


@dataclass(frozen=True, slots=True)
class ForecastClose:
    """What one price slot's close did to D10's baseline (mirrors `AccountingClose`)."""

    window_updated: bool = False
    #: `True` only on the slot the day's mean confidence first crosses D10's
    #: `OFFER_CONFIDENCE` - an edge, computed by the adapter's own memory of
    #: whether it had already crossed it, never a level (D10 §8's event).
    baseline_ready: bool = False
    confidence: float = 0.0
    #: The persisted `BaselineState`, encoded - set only when `window_updated`,
    #: the same "state travels back through the close" shape `AccountingClose`
    #: uses so `plan()` can write it into `EngineState.forecasts` without this
    #: module importing `core/forecasts` (INV-2's one-way direction).
    state: Mapping[str, Any] = field(default_factory=dict)


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
        constraints: Sequence[Constraint] = (),
        zones: Sequence[ZoneSpec] = (),
        accounting: AccountingHook | None = None,
        forecasts: ForecastHook | None = None,
    ) -> None:
        """Wire one site. Nothing here reads a store or a state (INV-3).

        `constraints` are the site's beyond its own hard limits - its circuits
        and its groups - built by the runtime from the
        subentries (D7 §5.5 step 2). `zones` are kept separately: unlike a
        circuit or a group, a `Zone` constraint needs this tick's own prices
        and outdoor temperature, so `self._zones` holds the specs and
        `_zone_constraints` builds fresh `Zone` objects every tick (WP5.3,
        the same "built fresh, not structural" precedent as
        `_cycle_reservations`). The site fuse and the phase limits are always
        there, and the walk takes every constraint in D6 §2's order: site →
        circuit → phase → group → zone, outermost physical limit first.
        """
        self.site = site
        self._meter = meter
        self._tariff = tariff
        self._configured: tuple[Load, ...] = ()
        self._loads: tuple[Load, ...] = ()
        self._constraints: tuple[Constraint, ...] = ()
        self._zones: tuple[ZoneSpec, ...] = tuple(zones)
        self._accounting = accounting
        self._forecasts = forecasts
        #: `_planned_kwh` memoised per load, keyed on the plan's own `built_at`
        #: (perf only, WP6.1's own gap found while profiling the benchmark
        #: runner - D-0321): a plan is re-cut roughly every 900 s but `_warnings`
        #: asks the same `[window_start, window_end)` of it every 10 s tick, so
        #: without this the slot walk repeats ~90× for an unchanged answer.
        self._planned_kwh_cache: dict[
            str, tuple[datetime, dict[tuple[datetime, datetime], float]]
        ] = {}
        #: One base `LoadCtx` per load for the tick in progress, keyed against that
        #: tick's own `Inputs` (`_ctx`).
        self._ctx_inputs: Inputs | None = None
        self._ctx_bases: dict[tuple[str, bool], LoadCtx] = {}
        self.set_loads(loads)
        self.set_constraints(constraints)

    @property
    def loads(self) -> tuple[Load, ...]:
        """The site's loads, in the order the walk and the budget spend them."""
        return self._loads

    def set_loads(self, loads: Sequence[Load]) -> None:
        """Replace the configured loads, in priority order (D7 §2's hot add/remove/update).

        The site's collaborators - `WindowMeter`, the tariff `Evaluator`, the
        accounting hook - own state of their own and are untouched; only the
        loads a tick walks change. `_apply_load_knobs` re-derives `_loads` from
        `_configured` every tick regardless, so a call between ticks is enough.
        """
        self._configured = tuple(
            sorted(loads, key=lambda load: (-load.config.priority, load.load_id))
        )
        self._loads = self._configured

    def set_constraints(self, constraints: Sequence[Constraint]) -> None:
        """Replace the constraints beyond the site's own hard limits (D7 §2).

        The site fuse and the phase limits are always there; `constraints` are
        the rest - circuits today, groups and zones later - walked in D6 §2's
        order: site → circuit → phase → group → zone.
        """
        self._constraints = tuple(
            sorted(
                (
                    SiteFuse(self.site.electrical.fuse_w()),
                    PhaseLimit(self.site.electrical),
                    *constraints,
                ),
                key=lambda constraint: _SCOPE_ORDER.index(constraint.scope),
            )
        )

    def set_zones(self, zones: Sequence[ZoneSpec]) -> None:
        """Replace the zone specs a tick builds fresh `Zone` constraints from."""
        self._zones = tuple(zones)

    def _apply_load_knobs(self, knobs: Knobs) -> None:
        """Put this tick's per-load knob parameters over the configured loads (D8 §5.5).

        A pure function of the constructor's loads and the knobs - the same
        inputs give the same loads - kept on the engine so every step of the
        tick reads one tuple. A comfort key rebuilds the target profile from the
        merged parameters, the way the flow built it (`profile_from_params`).
        """
        if not knobs.load_params:
            self._loads = self._configured
            return
        out: list[Load] = []
        for load in self._configured:
            overrides = knobs.load_params.get(load.load_id)
            if not overrides:
                out.append(load)
                continue
            params = {**load.config.params, **overrides}
            target = load.config.target
            if any(key in overrides for key in _TARGET_KEYS):
                target = profile_from_params(params) or target
            out.append(replace(load, config=replace(load.config, params=params, target=target)))
        self._loads = tuple(out)

    def _cycle_reservations(
        self, load_states: Mapping[str, LoadState]
    ) -> tuple[CycleReservation, ...]:
        """Return one `CycleReservation` per appliance cycle under way this tick (D6 §2, §5.7).

        Built fresh every tick, unlike `self._constraints` (circuits, groups -
        structural, only rebuilt on a subentry change): whether a cycle is
        `started`/`running` is this tick's own observation, and INV-59 protects
        the two alike (D4 §5.13). A type with no `profile()` method - every type
        but `appliance_cycle` - is skipped by the same duck-typed dispatch
        `Load.observe()` uses for `legionella` (D-0295).
        """
        out: list[CycleReservation] = []
        for load in self._loads:
            state = load_states.get(load.load_id)
            if state is None or state.cycle is None or not state.cycle.active:
                continue
            profile_fn = getattr(load.device_type, "profile", None)
            if profile_fn is None:
                continue
            profile = profile_fn(load.config, state)
            out.append(
                CycleReservation(
                    load_id=load.load_id,
                    power_w=profile.mean_w,
                    running=True,
                    started_at=state.cycle.started_at,
                )
            )
        return tuple(out)

    def _zone_constraints(self, curves: Curves | None, outdoor_c: float | None) -> tuple[Zone, ...]:
        """Return one `Zone` per configured zone, costed for this tick (D6 §5.7).

        Built fresh every tick, unlike `self._constraints`: the cost ranking
        needs this tick's own prices per carrier and outdoor temperature. The
        choice, dwell and candidate are not passed here - `allocate()` seeds
        them from `AllocState.zone_choice` itself (D6 §7), the same as it
        already does for `GroupCap.seed`.
        """
        if not self._zones:
            return ()
        prices = {} if curves is None else curves.import_
        return tuple(spec.build(prices, outdoor_c) for spec in self._zones)

    # ----------------------------------------------------------------- the tick #

    def tick(self, state: EngineState, inputs: Inputs) -> tuple[EngineState, Snapshot, Effects]:
        """Run one control loop and publish, whatever happens (D7 §5.1, INV-44).

        The order of §5.1 is the order of `_run`; this wrapper is the error
        budget of §2: an exception outside a load is the engine's own bug, so the
        counter moves, the previous snapshot is republished, and three in a row
        put the site in safe mode with every load released (INV-64).
        """
        self._apply_load_knobs(inputs.knobs)
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
        # The switch's position as of this tick: the next tick's edge, and what a
        # restart reads before it may write anything (D7 §5.5, D-0361).
        edges["site_active"] = "1" if knobs.active else "0"

        # -- 2. the meter -------------------------------------------------- #
        views = self._views(load_states, inputs, active, failed)
        meter = self._meter.sample(now, inputs.meter, tuple(views.values()))
        load_meters = self._sample_load_meters(state, inputs, views, meter)
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
            runtime = replace(
                runtime,
                windows_pending=(*runtime.windows_pending, *meter.closed)[-_WINDOWS_KEPT:],
            )

        # -- 4. the ceiling ------------------------------------------------ #
        eps_base = site.budget.eps_base_kwh if knobs.eps_base_kwh is None else knobs.eps_base_kwh
        eps_kwh = eps_for_window(eps_base, meter.window_min)
        risk = self._tariff.state().risk if knobs.risk is None else knobs.risk
        ceiling = self._tariff.ceiling_kwh(now, knobs.target, risk, eps_kwh)

        # -- 5. the budget ------------------------------------------------- #
        hard, runtime = self._hard_limits(now, meter, runtime)
        controlled_planned_kwh = sum(
            plan.kwh_between(now, now + timedelta(hours=meter.t_rem_h))
            for plan in state.plans.plans.values()
        )
        budget = build_budget(
            ceiling,
            meter,
            hard.p_hard_w(),
            pi,
            site.budget,
            _baseline(inputs),
            controlled_planned_kwh,
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
            circuits=_circuit_watts(inputs),
        )
        grants, report, alloc_state = allocate(
            ctx,
            (
                *self._constraints,
                ContractedPowerLimit(hard.contracted),
                *self._cycle_reservations(load_states),
                *self._zone_constraints(inputs.curves, inputs.outdoor_c),
                *_external_limits(inputs.events, now),
            ),
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
        events.extend(_domain_events(edges, ladder_state, report, observations, failed, budget))
        events.extend(_level_events(edges, self._tariff, budget))
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
                load_states,
                inputs,
                grants,
                observations,
                results,
                views,
                failed,
                frozen,
                load_meters,
                alloc_state.starved_since,
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
            load_meters=load_meters,
            alloc=alloc_state,
            grants=grants,
            events=replace(state.events, edges=edges),
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
                command = _command_of(load.load_id, result, after.gate)
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
            events.append(
                HaEvent(
                    EventKind.SAFE_MODE,
                    {
                        "entered": True,
                        "reason": f"{type(err).__name__}: {err}",
                        "failures": failures,
                    },
                )
            )
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
        """Return one load's context for this tick (D4 §5.1).

        The base context - no transport budget of the load's own, no plan delta,
        no desired option - is the same for every step of one tick, so it is built
        once per load and kept against this tick's own `Inputs` object (held, so its
        `id` cannot be reused while the entry lives); the apply step's context is
        that base with its three fields replaced.
        """
        if self._ctx_inputs is not inputs:
            self._ctx_inputs = inputs
            self._ctx_bases = {}
        base = self._ctx_bases.get((load.load_id, active))
        if base is None:
            base = self._build_ctx(load, inputs, active=active)
            self._ctx_bases[(load.load_id, active)] = base
        if budget is None and setpoint_delta == 0.0 and desired is None:
            return base
        return replace(
            base,
            budget=inputs.transport if budget is None else budget,
            setpoint_delta=setpoint_delta,
            desired=desired,
        )

    def _build_ctx(self, load: Load, inputs: Inputs, *, active: bool) -> LoadCtx:
        row = inputs.loads.get(load.load_id)
        return LoadCtx(
            now=inputs.now,
            reads=row.reads if row is not None else Reads(at=inputs.now),
            electrical=inputs.site.electrical,
            budget=inputs.transport,
            site_active=active,
            presence=_presence_now(inputs),
            setpoint_delta=0.0,
            desired=None,
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
        off releases every load on the edge (PLAN §7 dec. 20). Both undo only
        powerplan's own recorded writes, and the edge out of control is the last
        write a site that is off makes (INV-26, INV-27, `design/DECISIONS.md`
        D-0360). The switch's edge is the switch's own, read against the position
        the last tick recorded - safe mode releases on its own path (D7 §2).
        """
        states = dict(state.loads)
        commands: list[LoadCommand] = []
        dirty: set[Section] = set()
        was_active = state.events.edges.get("site_active")
        site_edge = was_active == "1" and not inputs.knobs.active
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
                command = _command_of(load.load_id, result, after.gate)
                if command is not None:
                    commands.append(command)
            elif edge is not None and edge.restore:
                after, result = load.restore(after, ctx, reason=edge.reason)
                reasons.append(f"{load.load_id}: restored ({result.reason})")
                command = _command_of(load.load_id, result, after.gate)
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
            wanted = None if plan is None else plan.desired_state_at(inputs.now)
            ctx = self._ctx(
                load,
                inputs,
                active=active,
                budget=budget,
                stage=ladder.stage,
                setpoint_delta=_setpoint_delta_of(wanted),
                desired=_desired_of(wanted),
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
            command = _command_of(load.load_id, result, after.gate)
            if command is not None and not _said_before(before.gate, result):
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

    def _cached_planned_kwh(
        self, load_id: str, plan: Plan | None, start: datetime, end: datetime
    ) -> float:
        """Return `_planned_kwh(plan, start, end)`, memoised per plan (perf, D-0321).

        `_warnings` asks the same handful of `[start, end)` windows of the same
        plan every tick until the next replan (~90× at 10 s ticks); the plan's
        own `built_at` is the cache's invalidation key, so a stale entry can
        never survive a replan.
        """
        if plan is None:
            return 0.0
        cached = self._planned_kwh_cache.get(load_id)
        if cached is None or cached[0] != plan.built_at:
            cached = (plan.built_at, {})
            self._planned_kwh_cache[load_id] = cached
        by_window = cached[1]
        value = by_window.get((start, end))
        if value is None:
            value = _planned_kwh(plan, start, end)
            by_window[(start, end)] = value
        return value

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
        """Return the peak warnings for the coming windows (D7 §5.4).

        The expectation for a coming window's uncontrolled term is D10's baseline
        when it clears `BASELINE_CONFIDENCE`, else the EMA held for the window
        (D-0319) - plus what the plans intend to move in it, plus what an
        urgent demand will take whether it is planned or not.
        """
        cfg = inputs.site.engine
        warnings: list[SiteWarning] = []
        events: list[HaEvent] = []
        notes: list[Notification] = []
        warned = set(runtime.peak.warned)
        ema = runtime.peak.ema_w
        baseline = inputs.forecast_baseline

        for start, end in _coming_windows(meter, cfg.warn_horizon_h):
            limit = self._window_ceiling_kwh(start, end, inputs.knobs.target)
            if math.isinf(limit) or limit <= 0.0:
                continue
            hours = (end - start).total_seconds() / 3600.0
            drivers: list[tuple[str, float]] = []
            expected = _expected_uncontrolled_kwh(baseline, ema, start, hours)
            if expected > 0.0:
                drivers.append(("uncontrolled", expected))
            for view in views:
                planned = self._cached_planned_kwh(
                    view.load_id, plans.get(view.load_id), start, end
                )
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
                            "cleared": True,
                            "window_start": start.isoformat(),
                            "expected_kwh": expected,
                            "ceiling_kwh": limit,
                            "drivers": [],
                            "advice": [],
                        },
                    )
                )
                # The clear reaches the notification policy too: the key resets and
                # the persistent notification goes (D8 §2, D-0276).
                notes.append(
                    Notification(
                        category="peak_warning",
                        key=f"peak:{start.isoformat()}",
                        params={"cleared": True, "window_start": start.isoformat()},
                        severity="info",
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

        retired, cleared_events, cleared_notes = _retire_warned(warned, meter, budget, cfg)
        warned -= retired
        events.extend(cleared_events)
        notes.extend(cleared_notes)

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
                        "cleared": True,
                        "key": runtime.peak.live,
                        "window_start": meter.window_start_utc.isoformat(),
                        "expected_kwh": meter.used_kwh,
                        "ceiling_kwh": budget.ceiling_kwh if budget is not None else 0.0,
                        "drivers": [],
                        "advice": [],
                    },
                )
            )
            notes.append(
                Notification(
                    category="peak_warning",
                    key=runtime.peak.live,
                    params={"cleared": True},
                    severity="info",
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
        load_meters: Mapping[str, LoadMeterState],
        starved_since: Mapping[str, datetime],
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
                lifetime_kwh=0.0
                if (meter_row := load_meters.get(load_id)) is None
                else meter_row.lifetime_kwh,
                energy_source=None if meter_row is None else meter_row.source.value,
                starved_s=0.0
                if (since := starved_since.get(load_id)) is None
                else max(0.0, (inputs.now - since).total_seconds()),
                legionella_due_at=None if observation is None else observation.legionella_due_at,
            )
        return out

    # ------------------------------------------------------------ the planner #

    def plan(  # noqa: PLR0912, PLR0915 - D7 §5.2's cycle, kept in one place and in order
        self, state: EngineState, inputs: Inputs
    ) -> tuple[EngineState, PlanReport, Effects]:
        """Run one planning cycle (D7 §5.2). Fetches nothing: the runtime did the I/O.

        The cycle rebuilds every load's plan, adopts past the hysteresis of
        D5 §5.9, and closes the accounting slots that ended since the last cycle -
        oldest first, exactly once each, and **never** in the tick (INV-68).
        """
        self._apply_load_knobs(inputs.knobs)
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
                            "mode": plan.mode.value,
                            "planned_kwh": plan.planned_kwh,
                            "cost": str(plan.cost_estimate.amount),
                            "currency": plan.cost_estimate.currency,
                            "next_start": _iso(plan.next_active(now)),
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
                            "shortfall_kwh": _shortfall_kwh(site_plan.plans[load_id]),
                            "reason": site_plan.plans[load_id].reason,
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

        runtime, closes, forecast_closes, load_meters = self._close_slots(state, inputs, views)
        accounting = dict(state.accounting)
        month_closed: str | None = None
        for close in closes:
            accounting = dict(close.state) if close.state else accounting
            month_closed = close.month_closed or month_closed
            if close.month_closed is not None:
                events.append(
                    HaEvent(
                        EventKind.MONTH_CLOSED,
                        {"month": close.month_closed, "period": close.month_closed},
                    )
                )
                period_event = self._period_closed_event(close.month_closed)
                if period_event is not None:
                    events.append(period_event)
        if closes:
            dirty.add(Section.ACCOUNTING)
            reasons.append(
                f"accounting: closed {len(closes)} slot(s) up to {_iso(runtime.closed_to)}"
            )
        forecasts_state = dict(state.forecasts)
        for forecast_close in forecast_closes:
            if forecast_close.baseline_ready:
                events.append(
                    HaEvent(EventKind.BASELINE_READY, {"confidence": forecast_close.confidence})
                )
            if forecast_close.window_updated:
                forecasts_state = dict(forecast_close.state)
        if forecasts_state != dict(state.forecasts):
            dirty.add(Section.FORECASTS)

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
            load_meters=load_meters,
            plans=plans,
            accounting=accounting,
            forecasts=forecasts_state,
            events=replace(state.events, edges=edges),
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

    def _period_closed_event(self, month_key: str) -> HaEvent | None:
        """Return `period_closed` for the capacity period that closed with `month_key` (D2 §2).

        D11's ledger month and D2's tariff period are the same calendar
        boundary for every preset shipped so far (`period: month`, never
        `year`, `design/DECISIONS.md`) - a yearly-period preset would need its
        own edge, deferred until one ships. `history.counterfactual()` is
        already fed by the accounting hook's own `close_slot` just above, so
        the pair of bills is meaningful the same tick the ledger rolls.
        """
        try:
            at = datetime.fromisoformat(f"{month_key}-01T00:00:00+00:00")
        except ValueError:
            return None
        period = self._tariff.period(at)
        actual = self._tariff.bill(period)
        counterfactual = self._tariff.bill(period, self._tariff.history.counterfactual())
        savings = counterfactual.capacity_fee.amount - actual.capacity_fee.amount
        return HaEvent(
            EventKind.PERIOD_CLOSED,
            {
                "period": month_key,
                "level": actual.level.name,
                "metric_kw": actual.metric_kw,
                "fee": str(actual.capacity_fee.amount),
                "counterfactual_fee": str(counterfactual.capacity_fee.amount),
                "capacity_savings": str(savings),
            },
        )

    def _close_slots(
        self, state: EngineState, inputs: Inputs, views: Sequence[LoadView]
    ) -> tuple[
        RuntimeState,
        tuple[AccountingClose, ...],
        tuple[ForecastClose, ...],
        Mapping[str, LoadMeterState],
    ]:
        """Close every price slot that ended since the last cycle, oldest first.

        A cycle skipped for an hour closes the backlog on the next one, in order,
        exactly once each (D7 §9 16). Each close carries what D3's load meters
        integrated for the slot and the site's own integral; the meters are
        acknowledged up to the last slot closed (D3 §5.12). Without a hook the
        cursor still advances and the meters are still acknowledged, so no slot
        is closed twice when one lands.
        """
        runtime = state.runtime
        if inputs.curves is None:
            return runtime, (), (), state.load_meters
        curve = inputs.curves.import_.get(Carrier.ELECTRICITY)
        if curve is None:
            return runtime, (), (), state.load_meters
        cursor = runtime.closed_to
        if cursor is None:
            # A fresh site: nothing before the meters' first slot is a slot at all.
            # The curve reaches back a day and a slot it covers but no meter saw
            # would be priced at 0 kWh and could open the ledger in the wrong month.
            cursor = _first_metered(state.load_meters)
            if cursor is None:
                return runtime, (), (), state.load_meters
        closed: list[AccountingClose] = []
        forecast_closes: list[ForecastClose] = []
        last: datetime | None = None
        handed = runtime.windows_closed_to
        by_id = {view.load_id: view for view in views}
        for slot in curve.slots:
            if slot.end > inputs.now or slot.end <= cursor:
                continue
            last = slot.end
            # The oldest closed window not yet handed over whose end this slot has
            # reached. A window that closed on the integral after the quarter's
            # plan rides on the next slot rather than being lost (D-0267).
            window = _window_due(runtime.windows_pending, slot.end, handed)
            if window is not None:
                handed = _window_end(window)
            if self._accounting is not None or self._forecasts is not None:
                close = self._slot_close(state, inputs, slot, by_id, window)
                if self._accounting is not None:
                    closed.append(self._accounting.close_slot(close))
                if self._forecasts is not None:
                    forecast_closes.append(self._forecasts.close_slot(close))
        if last is None:
            return runtime, (), (), state.load_meters
        acked = {
            load_id: _acked(load_id, meter_state, last)
            for load_id, meter_state in state.load_meters.items()
        }
        return (
            replace(
                runtime,
                closed_to=last,
                slots_closed=runtime.slots_closed + max(len(closed), 1 if last else 0),
                windows_closed_to=handed,
                windows_pending=tuple(
                    window
                    for window in runtime.windows_pending
                    if handed is None or _window_end(window) > handed
                ),
            ),
            tuple(closed),
            tuple(forecast_closes),
            acked,
        )

    def _slot_close(
        self,
        state: EngineState,
        inputs: Inputs,
        slot: Slot,
        views: Mapping[str, LoadView],
        window: ClosedWindow | None,
    ) -> SlotClose:
        """Assemble one slot's close from the meters and the views (D7 §5.2)."""
        assert inputs.curves is not None
        site_import = _slot_of(state.load_meters.get(SITE_IMPORT), slot.start)
        site_export = _slot_of(state.load_meters.get(SITE_EXPORT), slot.start)
        estimated = any(
            row is not None and row.confidence != "exact" for row in (site_import, site_export)
        )
        loads = {
            load.load_id: SlotLoad(
                load_id=load.load_id,
                mode=views[load.load_id].mode
                if load.load_id in views
                else self.load_mode(load.load_id, state, inputs),
                demand=views[load.load_id].demand if load.load_id in views else None,
                level_now=views[load.load_id].level_now if load.load_id in views else None,
                slot=_slot_of(state.load_meters.get(load.load_id), slot.start),
            )
            for load in self._loads
        }
        return SlotClose(
            start=slot.start,
            end=slot.end,
            now=inputs.now,
            curves=inputs.curves,
            site_import_kwh=0.0 if site_import is None else site_import.kwh,
            site_export_kwh=0.0 if site_export is None else site_export.kwh,
            site_confidence="estimated" if estimated or site_import is None else "exact",
            outdoor_c=inputs.outdoor_c,
            loads=loads,
            window_closed=window,
            presence=_presence_now(inputs),
        )

    def reset_window_anchor(
        self, state: EngineState, register_kwh: float, now: datetime, reason: str
    ) -> EngineState:
        """Re-anchor the window on the current register (D3 `reanchor`, D8 §5.7)."""
        self._meter.reanchor(register_kwh, now, reason)
        return replace(state, meter=self._meter.state())

    def load_mode(self, load_id: str, state: EngineState, inputs: Inputs) -> Mode:
        """Return a load's effective mode now, for a close without a view."""
        for load in self._loads:
            if load.load_id == load_id:
                return load.mode_now(
                    state.loads.get(load_id, LoadState()),
                    self._ctx(load, inputs, active=inputs.knobs.active),
                )
        return Mode.OFF

    def _sample_load_meters(
        self,
        state: EngineState,
        inputs: Inputs,
        views: Mapping[str, ControlledView],
        meter: MeterSnapshot,
    ) -> dict[str, LoadMeterState]:
        """Integrate every load's slot, and the site's own, one sample (D3 §5.12, D7 §5.1 step 2).

        A frozen tick still integrates: the ledger wants the energy whatever the
        window meter could see. The site's import and export ride on two meters
        of their own, fed the grid reading, so D11 gets the slot's import from the
        same integral the loads use (D-0267).
        """
        minutes = _slot_minutes(inputs)
        out: dict[str, LoadMeterState] = {}
        for load in self._loads:
            view = views.get(load.load_id)
            if view is None:
                previous = state.load_meters.get(load.load_id)
                if previous is not None:
                    out[load.load_id] = previous
                continue
            row = LoadMeter(
                LoadMeterConfig(load_id=load.load_id, nameplate_w=load.config.nameplate_w),
                state.load_meters.get(load.load_id),
            )
            reads = inputs.loads.get(load.load_id)
            energy = None if reads is None else reads.reads.get(Role.ENERGY)
            row.sample(inputs.now, view, None if energy is None else energy.reading, minutes)
            out[load.load_id] = row.state()
        grid_w = meter.grid_w
        for load_id, watts in (
            (SITE_IMPORT, None if grid_w is None else max(grid_w, 0.0)),
            (SITE_EXPORT, None if grid_w is None else max(-grid_w, 0.0)),
        ):
            row = LoadMeter(LoadMeterConfig(load_id=load_id), state.load_meters.get(load_id))
            row.sample(
                inputs.now,
                ControlledView(
                    load_id=load_id, measured_w=watts, commanded_w=None, settling=False, phases=None
                ),
                None,
                minutes,
            )
            out[load_id] = row.state()
        return out


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


def _baseline(inputs: Inputs) -> Baseline | None:
    """Return D6's own view of D10's baseline for the budget (D-0319).

    `inputs.forecasts` is D5's narrow view (`outdoor_c`/`surplus_w`/`baseline_w`,
    D-0316) and cannot answer `budget()`'s questions - `forecast_baseline` is the
    dedicated field `runtime.py` builds instead, the same pattern as
    `forecast_confidence`/`forecast_ready`.
    """
    return inputs.forecast_baseline


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


#: The site's own slot integrals live beside the loads' under these ids (D-0267).
SITE_IMPORT: Final = "__site_import__"
SITE_EXPORT: Final = "__site_export__"
#: Closed windows kept for the planning loop to hand to D11 - two days of hours.
_WINDOWS_KEPT: Final = 48
#: The slot length D3's load meters use when no curve says otherwise (D1: quarter hours).
_DEFAULT_SLOT_MINUTES: Final = 15


def _slot_minutes(inputs: Inputs) -> int:
    """Return the price slot length in force, from the curve at `now` (D3 §5.12)."""
    if inputs.curves is None:
        return _DEFAULT_SLOT_MINUTES
    curve = inputs.curves.import_.get(Carrier.ELECTRICITY)
    slot = None if curve is None else curve.price_at(inputs.now)
    return _DEFAULT_SLOT_MINUTES if slot is None else slot.minutes


def _slot_of(meter_state: LoadMeterState | None, start: datetime) -> LoadSlot | None:
    """Return the closed slot starting at `start`, if the meter has one pending."""
    if meter_state is None:
        return None
    return next((row for row in meter_state.pending_closed if row.start_utc == start), None)


def _acked(load_id: str, meter_state: LoadMeterState, upto: datetime) -> LoadMeterState:
    """Return the meter state with the slots D11 has recorded dropped (D3 §5.12)."""
    row = LoadMeter(LoadMeterConfig(load_id=load_id), meter_state)
    row.ack(upto)
    return row.state()


def _retire_warned(
    warned: set[datetime], meter: MeterSnapshot, budget: Budget | None, cfg: EngineCfg
) -> tuple[set[datetime], list[HaEvent], list[Notification]]:
    """Clear the warned windows that are no longer coming (D7 §5.4, D8 §2, D-0276).

    A warned window that is now the current one, or already over, is cleared
    explicitly - the projection says the hour is fine, or the hour has passed -
    never dropped in silence, so the notification it raised goes with it. The
    current window stays warned while the live projection is still hot.
    """
    retired: set[datetime] = set()
    events: list[HaEvent] = []
    notes: list[Notification] = []
    for start in sorted(w for w in warned if w <= meter.window_start_utc):
        current = start == meter.window_start_utc
        if (
            current
            and budget is not None
            and budget.projected_kwh >= cfg.clear_fraction * budget.ceiling_kwh
        ):
            continue
        retired.add(start)
        expected_now = budget.projected_kwh if current and budget is not None else meter.used_kwh
        events.append(
            HaEvent(
                EventKind.PEAK_WARNING,
                {
                    "active": False,
                    "cleared": True,
                    "window_start": start.isoformat(),
                    "expected_kwh": expected_now,
                    "ceiling_kwh": budget.ceiling_kwh if budget is not None else 0.0,
                    "drivers": [],
                    "advice": [],
                },
            )
        )
        notes.append(
            Notification(
                category="peak_warning",
                key=f"peak:{start.isoformat()}",
                params={"cleared": True, "window_start": start.isoformat()},
                severity="info",
            )
        )
    return retired, events, notes


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


def _external_limits(events: Sequence[Event], now: datetime) -> tuple[ExternalLimit, ...]:
    """Return one `ExternalLimit` per active load-limit event (D6 §5.8).

    From D1's own `load_limit` announcements (`design/DECISIONS.md` D-0327):
    the named loads - or the whole site, with none named - may take
    `max_w` and no more while the event is active, §14a's own 4.2 kW
    example. Built fresh every tick, the same "not structural" precedent
    as a zone or a cycle reservation: an event is a fact this tick reads,
    not something a subentry configures.
    """
    out: list[ExternalLimit] = []
    for event in events:
        if event.kind is not PricingEventKind.LOAD_LIMIT or not event.is_active_at(now):
            continue
        max_w = event.payload.get("max_w")
        if not isinstance(max_w, int | float):
            continue
        loads = event.payload.get("loads") or ()
        out.append(
            ExternalLimit(
                float(max_w),
                loads=frozenset(str(load_id) for load_id in loads),
                event_id=event.id,
            )
        )
    return tuple(out)


def _expected_uncontrolled_kwh(
    baseline: Baseline | None, ema: float | None, start: datetime, hours: float
) -> float:
    """Return a coming window's uncontrolled term (D7 §5.4, D-0319).

    D10's baseline once it clears `BASELINE_CONFIDENCE`, else the EMA held for
    the window - the same gate `budget()` uses for the projection (D6 §2).
    """
    if baseline is not None and baseline.confidence >= BASELINE_CONFIDENCE:
        return baseline.energy_kwh(start, hours)
    return 0.0 if ema is None else ema / 1000.0 * hours


def _planned_kwh(plan: Plan | None, start: datetime, end: datetime) -> float:
    """Return what a plan intends to move inside `[start, end)` (D5 §4)."""
    if plan is None:
        return 0.0
    total = 0.0
    for slot in plan.slots_between(start, end):
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
        "cleared": not active,
        # `warning`, not `kind`: `kind` is the bus envelope's (D8 §2).
        "warning": warning.kind,
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


def _plug_edges(edges: dict[str, str], observations: Mapping[str, Any]) -> list[HaEvent]:
    """Return `ev_connected` for every car plugged in or unplugged this tick (D4 §5.11).

    A demand change the planner must see now (D7 §5.2): one event per edge in
    either direction, none for what the first observation happened to find.
    """
    events: list[HaEvent] = []
    for load_id, observation in observations.items():
        connected = getattr(observation, "connected", None)
        if connected is None:
            continue
        key = f"connected:{load_id}"
        current = "1" if connected else "0"
        previous = edges.get(key)
        if previous == current:
            continue
        edges[key] = current
        if previous is not None:
            events.append(
                HaEvent(
                    EventKind.EV_CONNECTED,
                    {
                        "load": load_id,
                        "connected": bool(connected),
                        "soc": getattr(observation, "soc", None),
                    },
                )
            )
    return events


def _legionella_events(edges: dict[str, str], observations: Mapping[str, Any]) -> list[HaEvent]:
    """Return `legionella` for every water heater's cycle edge this tick (D4 §5.12, D8 §5.6).

    Four one-way edges per load, `None` for a type with no cycle or one running
    its own programme (`Observation.legionella_*`): the lead window opening is
    `due`, the hold starting `started`, a hold finishing `completed`, and the
    cycle turning unable to finish in time `at_risk` - none of the four ever
    fires on the tick a load is first observed (§9 8's "none for what the first
    observation happened to find", matching `_plug_edges`), which also keeps the
    adoption anchor (D-0203) from reading as a completed cycle.
    """
    events: list[HaEvent] = []
    for load_id, observation in observations.items():
        active = getattr(observation, "legionella_active", None)
        if active is None:
            continue
        for state_name, value in (
            ("due", active),
            ("started", getattr(observation, "legionella_in_progress", None)),
            ("at_risk", getattr(observation, "legionella_at_risk", None)),
        ):
            key = f"legionella_{state_name}:{load_id}"
            current = "1" if value else "0"
            previous = edges.get(key)
            edges[key] = current
            if previous is not None and previous != current and value:
                events.append(HaEvent(EventKind.LEGIONELLA, {"load": load_id, "state": state_name}))
        completed = getattr(observation, "legionella_last_completed", None)
        key = f"legionella_completed:{load_id}"
        current = "" if completed is None else completed.isoformat()
        previous = edges.get(key)
        edges[key] = current
        if previous is not None and previous != current and completed is not None:
            events.append(HaEvent(EventKind.LEGIONELLA, {"load": load_id, "state": "completed"}))
    return events


def _cycle_events(edges: dict[str, str], observations: Mapping[str, Any]) -> list[HaEvent]:
    """Return `cycle` for every appliance cycle's phase edge this tick (D4 §5.13, D8 §5.6).

    One string per load, `Observation.cycle_state` - `None` for a type with no
    cycle at all, `""` for one with nothing notify-worthy this tick - the same
    single-field edge shape `_plug_edges` uses for `connected`, simpler than
    legionella's four because a cycle's four names are already one mutually
    exclusive value (`CycleState.notify_state`). None of the four fires on the
    tick a load is first observed, matching every other edge here.
    """
    events: list[HaEvent] = []
    for load_id, observation in observations.items():
        current = getattr(observation, "cycle_state", None)
        if current is None:
            continue
        key = f"cycle:{load_id}"
        previous = edges.get(key)
        edges[key] = current
        if previous is not None and previous != current and current:
            started_at = getattr(observation, "cycle_started_at", None)
            events.append(
                HaEvent(
                    EventKind.CYCLE,
                    {
                        "load": load_id,
                        "state": current,
                        "start_at": None if started_at is None else started_at.isoformat(),
                    },
                )
            )
    return events


def _circuit_events(edges: dict[str, str], report: AllocReport) -> list[HaEvent]:
    """Return one `breach` event per circuit whose fuse is newly exceeded (D6 §8).

    D6's `circuit_breach(circuit, members)` is D8 §5.6's `breach` row with
    `breach = "circuit"` and the circuit as its scope; the table is the members'
    rows only, because the breach says nothing about the rest of the house (INV-60).
    """
    events: list[HaEvent] = []
    for key, circuit in sorted(report.circuits.items()):
        breached = "1" if circuit.breach else "0"
        if edges.get(f"circuit:{key}") == breached:
            continue
        edges[f"circuit:{key}"] = breached
        if not circuit.breach:
            continue
        members = set(circuit.members)
        events.append(
            HaEvent(
                EventKind.BREACH,
                {
                    "breach": "circuit",
                    "excess_w": max(0.0, (circuit.measured_w or 0.0) - circuit.limit_w),
                    "scope": key,
                    "limit_w": circuit.limit_w,
                    "measured_w": circuit.measured_w,
                    "sub_meter": circuit.sub_meter,
                    "members": list(circuit.members),
                    "table": [
                        {
                            "load": row.load,
                            "granted": row.granted_w,
                            "measured": row.measured_w,
                            "reserved": row.reserved_w,
                        }
                        for row in report.reserved
                        if row.load in members
                    ],
                },
            )
        )
    return events


def _domain_events(  # noqa: PLR0917 - one edge per D8 §5.6 row, in one place
    edges: dict[str, str],
    ladder: LadderState,
    report: AllocReport,
    observations: Mapping[str, Any],
    failed: Mapping[str, str],
    budget: Budget | None = None,
) -> list[HaEvent]:
    """Return the edge-triggered events of this tick (D7 §5.1 step 11, D8 §5.6)."""
    events: list[HaEvent] = _plug_edges(edges, observations)
    stage_key = f"{ladder.stage}:{reason_key(ladder.reason)}"
    previous = edges.get("stage")
    if previous != stage_key:
        edges["stage"] = stage_key
        old = int(previous.split(":", 1)[0]) if previous and previous[0].isdigit() else 0
        events.append(
            HaEvent(
                EventKind.STAGE_CHANGED,
                {
                    "old": old,
                    "new": ladder.stage,
                    "stage": ladder.stage,
                    "reason": ladder.reason,
                    "blunt": ladder.blunt,
                    "projected_kwh": None if budget is None else budget.projected_kwh,
                    "ceiling_kwh": None if budget is None else budget.ceiling_kwh,
                },
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
                        "breach": "window",
                        "excess_w": report.breach_w,
                        "scope": "site",
                        "breach_w": report.breach_w,
                        "deficit_w": report.deficit_w,
                        "table": [
                            {
                                "load": row.load,
                                "granted": row.granted_w,
                                "measured": row.measured_w,
                                "reserved": row.reserved_w,
                            }
                            for row in report.reserved
                        ],
                    },
                )
            )
    events.extend(_circuit_events(edges, report))
    events.extend(_legionella_events(edges, observations))
    events.extend(_cycle_events(edges, observations))
    comfort = ",".join(sorted(report.comfort))
    if edges.get("comfort") != comfort:
        edges["comfort"] = comfort
        granted = set(report.granted)
        for load_id in sorted(report.comfort):
            observation = observations.get(load_id)
            state = None if observation is None else observation.demand.comfort
            events.append(
                HaEvent(
                    EventKind.COMFORT_VIOLATION,
                    {
                        "load": load_id,
                        "current": None if state is None else state.current,
                        "floor": None if state is None else state.floor,
                        "served": load_id in granted,
                        "over_allowance": report.over_allowance,
                    },
                )
            )
    for load_id, observation in sorted(observations.items()):
        unhealthy = "1" if observation.health.unhealthy else "0"
        was = edges.get(f"unhealthy:{load_id}")
        if was == unhealthy:
            continue
        edges[f"unhealthy:{load_id}"] = unhealthy
        if observation.health.unhealthy or was == "1":
            events.append(
                HaEvent(
                    EventKind.DEVICE_UNHEALTHY,
                    {
                        "load": load_id,
                        "failures": observation.health.failures,
                        "last_error": observation.health.last_error,
                        "recovered": not observation.health.unhealthy,
                    },
                )
            )
    for load_id, error in sorted(failed.items()):
        if edges.get(f"unhealthy:{load_id}") == "1":
            continue
        edges[f"unhealthy:{load_id}"] = "1"
        events.append(
            HaEvent(
                EventKind.DEVICE_UNHEALTHY,
                {"load": load_id, "failures": 1, "last_error": error, "recovered": False},
            )
        )
    return events


def _shortfall_kwh(plan: Plan) -> float:
    """Return what a plan leaves unserved: the requirement times the uncovered share."""
    required = plan.required_kwh or 0.0
    return round(max(0.0, required * (1.0 - min(1.0, max(0.0, plan.coverage)))), 3)


def _level_events(edges: dict[str, str], tariff: TariffModel, budget: Budget) -> list[HaEvent]:
    """Return `level_changed` on the actual level's edge and on the projected one's (D8 §5.6)."""
    events: list[HaEvent] = []
    level = tariff.level()
    projected = tariff.projected_level(budget.projected_kwh)
    for edge, current, flag in (
        ("level_actual", level, False),
        ("level_projected", projected, True),
    ):
        was = edges.get(edge)
        if was == current.name:
            continue
        edges[edge] = current.name
        if was is None:
            continue
        events.append(
            HaEvent(
                EventKind.LEVEL_CHANGED,
                {
                    "old": was,
                    "new": current.name,
                    "metric_kw": current.metric_kw,
                    "fee": None if current.fee is None else str(current.fee.amount),
                    "projected": flag,
                },
            )
        )
    return events


def _circuit_watts(inputs: Inputs) -> dict[str, float | None]:
    """Return each sub-metered circuit's power this tick, `None` where its meter is blind.

    A sub-meter is judged like the site's power reading: OK and no older than the
    site meter's own stale cap (D3 §5.4). Blind or stale, the circuit falls back to
    its members' own measurements plus the unmetered allowance (D6 §8) - a stuck
    sub-meter may neither open the fuse nor slam it.
    """
    return {
        key: (
            sample.grid_w.value
            if sample.grid_w is not None and is_fresh(sample.grid_w, inputs.now, STALE_CAP_S)
            else None
        )
        for key, sample in inputs.circuits.items()
    }


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


def _command_of(load_id: str, result: ApplyResult, gate: GateState) -> LoadCommand | None:
    """Return the executor's work for one load, or `None` when there is none.

    Only a written or observed decision reaches `writegate.py`: a held or
    unchanged one has nothing to send, and the gate state is already threaded
    into the `LoadState` the engine returns. `current` is what the gate decided
    against, carried on the result, because the INFO line the executor logs is
    "old → new, why" - an observed decision's too, which has no command to name
    a role by (H.1 F-4: every observe line read "from None", D-0363).
    """
    if result.action is not Action.WRITTEN and result.action is not Action.OBSERVE:
        return None
    return LoadCommand(
        load_id=load_id,
        decision=Decision(
            action=result.action,
            command=result.command,
            value=result.value,
            current=result.current,
            reason=result.reason,
            gate=gate,
            budget=result.budget,
            blocking=result.blocking,
            verify_at=result.verify_at,
        ),
    )


#: A load context's own default presence, read once rather than by building a
#: throwaway `LoadCtx` per load per tick.
_DEFAULT_PRESENCE: Final[PresenceMode] = next(
    f.default
    for f in fields(LoadCtx)
    if f.name == "presence" and isinstance(f.default, PresenceMode)
)


def _said_before(gate: GateState, result: ApplyResult) -> bool:
    """Say whether an observed would-be write repeats the one last reported (H.1 F-4).

    The executor logs every observed decision it is handed, so an unchanged one is
    not handed over: a would-be value is reported once, however many ticks it
    stands, and the gate's record of it is persisted with the load (D-0363).
    """
    return result.action is Action.OBSERVE and gate.observed == result.value


def _presence_now(inputs: Inputs) -> PresenceMode:
    """Return the presence mode in force: the knob, else a load context's default."""
    if inputs.knobs.presence is not None:
        return inputs.knobs.presence
    return _DEFAULT_PRESENCE


def _window_end(window: ClosedWindow) -> datetime:
    """Return when a closed window ended."""
    return window.start_utc + timedelta(minutes=window.window_min)


def _window_due(
    pending: Sequence[ClosedWindow], slot_end: datetime, handed: datetime | None
) -> ClosedWindow | None:
    """Return the oldest closed window a slot ending at `slot_end` should carry to D11.

    One whose end the slot has reached and that has not been handed over yet; the
    windows arrive in order, so the oldest is the first that qualifies.
    """
    due = [
        window
        for window in pending
        if _window_end(window) <= slot_end and (handed is None or _window_end(window) > handed)
    ]
    return min(due, key=_window_end) if due else None


def _first_metered(meters: Mapping[str, LoadMeterState]) -> datetime | None:
    """Return the start of the earliest slot any load meter has integrated, if one has."""
    starts = [
        meter.pending_closed[0].start_utc if meter.pending_closed else meter.slot_start_utc
        for meter in meters.values()
    ]
    return min(starts) if starts else None


def _level_now(demand: Demand, reads: Reads) -> float | None:
    """Return the store's level: the comfort variable, or a state of charge.

    A thermal load's level is the temperature its comfort is measured on and an
    EV's is its SoC; both are what D5's `LoadView.level_now` means, and only the
    engine sees the reads (`design/DECISIONS.md` D-0234).
    """
    if demand.comfort is not None and demand.comfort.current is not None:
        return demand.comfort.current
    return reads.value(Role.SOC)


#: Below this many watts a grant is nothing (D6 §5.3).
_EPS_W: Final = 1e-6


def _quantiser(load: Load, state: LoadState, ctx: LoadCtx, mode: Mode) -> Any:
    """Return the load's own `quantise`, through its real control kind (D6 §5.3).

    The allocator is charged what the device will actually draw - the 6 A cliff
    when a stop is vetoed - and only the engine can build the `KindCtx` the kind
    needs (D5 §4's `Quantiser`, `design/DECISIONS.md` D-0160).
    """
    # Built on first use (D9 §5.13): the allocator asks about a quarter
    # of the loads it is handed a quantiser for, and `kind_ctx` is a pure function
    # of its arguments, so building it later changes nothing but the cost.
    built: list[KindCtx] = []

    def quantise(w: float, *, stop_ok: bool, session_active: bool) -> float:
        if not built:
            built.append(load.device_type.kind_ctx(load, state, ctx, grant=None, mode=mode))
        base = built[0]
        kind_ctx = (
            base
            if base.stop_ok is stop_ok and base.session_active is session_active
            else replace(base, stop_ok=stop_ok, session_active=session_active)
        )
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


def _setpoint_delta_of(desired: Any) -> float:
    """Return a slot's setpoint delta, 0 with none (D5 §2)."""
    return float(desired) if isinstance(desired, float | int) else 0.0


def _desired_of(desired: Any) -> Any:
    """Return a slot's desired option, `None` for a delta or no slot (D5 §2)."""
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


@dataclass(frozen=True, slots=True)
class _DayStats:
    """A curve's local-day figures, computed once per curve and day (D-0261)."""

    min_total: Decimal | None
    max_total: Decimal | None
    mean: Decimal | None
    spread: Decimal


#: The day figures of the curves in force, keyed by what identifies a curve
#: build; a new build evicts the old one's rows. The tick asked the same 96
#: slots for their mean and spread 360 times an hour.
_DAY_STATS: dict[tuple[Carrier, datetime, int, date], _DayStats] = {}


def _day_stats(carrier: Carrier, curve: PriceCurve, day: date, tz: tzinfo) -> _DayStats:
    """Return the curve's figures for the local `day`, memoised per build."""
    key = (carrier, curve.built_at, len(curve.slots), day)
    cached = _DAY_STATS.get(key)
    if cached is not None:
        return cached
    if len(_DAY_STATS) > _DAY_STATS_KEEP:
        _DAY_STATS.clear()
    totals = [row.total for row in curve.slots_between(*_local_day(day, tz))]
    stats = _DayStats(
        min_total=min(totals) if totals else None,
        max_total=max(totals) if totals else None,
        mean=curve.mean(day, tz) if totals else None,
        spread=curve.spread(day, tz),
    )
    _DAY_STATS[key] = stats
    return stats


#: Enough rows for a handful of carriers over the horizon's days.
_DAY_STATS_KEEP: Final = 64


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
        stats = _day_stats(carrier, curve, day, site.tz)
        carriers[carrier] = CarrierPrices(
            carrier=carrier,
            currency=curve.currency,
            now=None if slot is None else slot.total,
            next=None if following is None else following.total,
            min_today=stats.min_total,
            max_today=stats.max_total,
            mean_today=stats.mean,
            spread_today=stats.spread,
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
    return curve.has_known_after(midnight)


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
    """Return what D10 could answer this tick (D7 §4.1)."""
    forecasts = inputs.forecasts
    if forecasts is None:
        return ForecastStatus()
    return ForecastStatus(
        available=True,
        outdoor_c=forecasts.outdoor_c(inputs.now),
        surplus_w=forecasts.surplus_w(inputs.now),
        baseline_w=forecasts.baseline_w(inputs.now),
        baseline_ready=inputs.forecast_ready,
        confidence=inputs.forecast_confidence,
    )


@functools.lru_cache(maxsize=8)
def _decode_money(amount: str, currency: str) -> Money:
    """Return the `Money` `amount`/`currency` decode to, memoised (perf, D-0321).

    The ledger's month-to-date figure changes only when a slot closes, but this
    is read every tick for `Snapshot.accounting` (INV-44) - small and bounded:
    at most a handful of currencies are ever live in one site. `Money` has no
    `as_dict`/`from_dict` of its own (D7 §7's generic dataclass shape), so this
    is exactly what `_decode(Money,...)` would build, without its reflection.
    """
    return Money(Decimal(amount), currency)


#: `AccountingStatus`'s money beyond `cost` and `savings`: what the hook encodes
#: under `status` and the tick decodes back.
ACCOUNTING_MONEY_FIELDS: tuple[str, ...] = (
    "energy_cost",
    "export_credit",
    "capacity_fee",
    "energy_savings",
    "capacity_savings",
    "cf_cost",
    "previous_cost",
    "previous_savings",
    "lifetime_cost",
    "lifetime_savings",
)


@functools.lru_cache(maxsize=8)
def _decode_moment(text: str) -> datetime:
    """Return the instant an ISO string encodes, memoised like `_decode_money`."""
    return datetime.fromisoformat(text)


def _money_of(data: Mapping[str, Any] | None) -> Money | None:
    return None if data is None else _decode_money(data["amount"], data["currency"])


def _accounting_status(state: EngineState) -> AccountingStatus:
    """Return the ledger's section as of the last closed slot (D11, INV-68).

    The hook leaves its month-to-date figures under `status` in the opaque
    `accounting` section (D-0267); the tick republishes them and never computes
    any (INV-68). Every figure the hook encodes comes back - the month's start
    and the lifetime's too, which are the monetary sensors' `last_reset` and
    `since_install` (the tick read four keys and published the rest
    as `None`). A section written before WP U.4 has only those four until the
    next slot closes.
    """
    status = state.accounting.get("status") or {}
    month_start = status.get("month_start")
    since = status.get("since")
    return AccountingStatus(
        slots_closed=state.runtime.slots_closed,
        closed_to=state.runtime.closed_to,
        month_key=state.accounting.get("month_key"),
        month_start=None if month_start is None else _decode_moment(month_start),
        since=None if since is None else _decode_moment(since),
        cost=_money_of(status.get("cost")),
        savings=_money_of(status.get("savings")),
        confidence=str(status.get("confidence", "none")),
        pricing_confidence=str(status.get("pricing_confidence", "none")),
        energy_cost=_money_of(status.get("energy_cost")),
        export_credit=_money_of(status.get("export_credit")),
        capacity_fee=_money_of(status.get("capacity_fee")),
        energy_savings=_money_of(status.get("energy_savings")),
        capacity_savings=_money_of(status.get("capacity_savings")),
        cf_cost=_money_of(status.get("cf_cost")),
        estimated_share=float(status.get("estimated_share", 0.0)),
        previous_cost=_money_of(status.get("previous_cost")),
        previous_savings=_money_of(status.get("previous_savings")),
        lifetime_cost=_money_of(status.get("lifetime_cost")),
        lifetime_savings=_money_of(status.get("lifetime_savings")),
        per_load=dict(status.get("per_load", {})),
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
