"""What a strategy is allowed to know, and the headroom it plans into (D5 §4, §5.1).

Three types cross into this package from outside and are declared here because
D5 is the first domain that needs them and neither D2 nor D6 defines them
(`design/DECISIONS.md` D-0132):

* `LoadView` - the projection of a `Load` a planner may see: what it is called,
  how badly it wants power, what it can take, what it stores. D6 §4 names the
  same type and adds `quantise()` to it in WP0.7.
* `Curves` - every `(carrier, direction)` curve the site has, so `plan_all` can
  hand each load the pair its own carrier is priced in.
* `Forecasts` - the three questions D5 asks D10 (weather, surplus, baseline), as
  a `Protocol`, so D10's own class satisfies it without D5 importing D10.

**The headroom is the capacity axis entering the price axis** (INV-31). Per slot
it is the flat target for that window - D2's `target_w_at`, which carries no
slack and no free ride into the future, because tomorrow's peak has not happened
yet - less the uncontrolled baseline, less what higher-priority loads have
already reserved (INV-33). Outside the tariff's eligible windows there is no
ceiling at all and the headroom is infinite; the hard limits still bind, and they
are D6's (INV-1).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from ..loads import CalendarEvent, PresenceMode, TargetProfile
from ..loads.stores.base import StoreModel
from ..model import Carrier, Demand, Mode, Plan, PriceCurve, Slot
from ..pricing import Event, EventKind, HolidayCalendar, HysteresisPolicy
from ..tariffs import AUTO, Target

if TYPE_CHECKING:
    from ..loads import Load

__all__ = [
    "NO_HOLIDAYS",
    "CeilingSource",
    "Curves",
    "Forecasts",
    "Headroom",
    "LoadView",
    "PlanContext",
    "Quantiser",
    "SiteContext",
    "SitePlan",
    "with_rewards",
]


class CeilingSource(Protocol):
    """The two questions the headroom builder asks a tariff (D2 §3, D5 §5.1).

    A narrow view of D2's `TariffModel`: `Evaluator` satisfies it structurally,
    and nothing in D5 can reach the rest of the tariff by accident.
    """

    def target_w_at(self, t: datetime, target: Target) -> float:
        """Return the flat ceiling in W for the window containing `t`."""
        ...

    def eligible_windows(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime, float]]:
        """Return the eligible windows of `[start, end)` and their weights."""
        ...


class Forecasts(Protocol):
    """What D10 tells the planner (HLD §6.10). A forecast is never an authority."""

    def outdoor_c(self, t: datetime) -> float | None:
        """Return the forecast outdoor temperature at `t`."""
        ...

    def surplus_w(self, t: datetime) -> float:
        """Return the PV surplus expected at `t`, 0 with no production."""
        ...

    def baseline_w(self, t: datetime) -> float:
        """Return the uncontrolled load expected at `t`."""
        ...


class _NoHolidays:
    """The calendar a site without one has (D1 §2).

    A `TimeFilter` is evaluated against a calendar even when its holiday mode is
    `IGNORE`, so the planner always has one to hand. The real calendar is the
    `holidays` package with the site's country and D7 passes it in; the
    default here says "nothing is a holiday", which is what a filter that does
    not ask about holidays means anyway.
    """

    def is_holiday(self, day: date) -> bool:
        """Return `False`: without a calendar, no day is a holiday."""
        return False

    def name(self, day: date) -> str | None:
        """Return `None`: without a calendar, no day has a name."""
        return None


#: The calendar used when the site has none (`design/DECISIONS.md` D-0190).
NO_HOLIDAYS: Final[HolidayCalendar] = _NoHolidays()


@dataclass(frozen=True, slots=True)
class Headroom:
    """What each slot may take, keyed by the slot's UTC start (D5 §5.1).

    Immutable: `reserve()` returns the headroom the next, lower-priority load
    sees. A slot nobody has an opinion about is unconstrained - `inf`, not zero,
    because a missing ceiling is not a closed gate (INV-15's spirit; the real
    limits are D6's).
    """

    by_slot: Mapping[datetime, float] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        slots: Sequence[Slot],
        *,
        tariff: CeilingSource | None = None,
        target: Target = AUTO,
        forecasts: Forecasts | None = None,
        eps_w: float = 0.0,
        fraction: float = 1.0,
    ) -> Headroom:
        """Return the per-slot headroom for `slots` (D5 §5.1, INV-31).

        `target_w_at` already answers `inf` outside the tariff's eligible
        windows; `eligible_windows` is what a strategy reads to know *why*
        (`PlanContext.tariff_eligible`), and both come from the same evaluator so
        the two can never disagree.

        `eps_w` and `fraction` are the ceiling D6 actually defends: the target
        less D3's ε, at the ladder's stage-2 threshold. A plan cut to the bare
        target opened every planned full-power slot at 103 % of the ceiling and
        the ladder shed the bathrooms at the window boundary
        (`design/DECISIONS.md` D-0257).
        """
        room: dict[datetime, float] = {}
        for slot in slots:
            ceiling = math.inf if tariff is None else tariff.target_w_at(slot.start, target)
            baseline = 0.0 if forecasts is None else forecasts.baseline_w(slot.start)
            if math.isinf(ceiling):
                room[slot.start] = ceiling
                continue
            guarded = max(0.0, ceiling - eps_w) * fraction
            room[slot.start] = max(0.0, guarded - baseline)
        return cls(by_slot=room)

    def w_at(self, start: datetime) -> float:
        """Return the watts available in the slot starting at `start`."""
        return self.by_slot.get(start, math.inf)

    def reserve(self, taken: Mapping[datetime, float]) -> Headroom:
        """Return the headroom left after `taken` watts are reserved per slot."""
        if not taken:
            return self
        room = dict(self.by_slot)
        for start, watts in taken.items():
            room[start] = max(0.0, self.w_at(start) - watts)
        return replace(self, by_slot=room)


def with_rewards(curve: PriceCurve, events: Sequence[Event], *, participates: bool) -> PriceCurve:
    """Return `curve` with `reward` events folded into the price (D5 §2, §9 15).

    A demand-response reward is an ordinary price signal: turning down during the
    window earns `per_kwh`, so *consuming* during it costs that much more, and the
    cheapest way to make every strategy exploit it is to raise the price it sees.
    Nothing else in the planner needs to know events exist.

    Only for a load with `participate_in_events` - a household enrols a charger in
    a flexibility scheme, not its bathroom floor, and a load that is not enrolled
    earns nothing by turning down, so its price must not move (INV-31: strategies
    see the composed curve, and this is a composition of one more component).

    A slot is priced by what holds at its **start**, the same way every other price
    is keyed (`design/DECISIONS.md` D-0198).
    """
    if not participates:
        return curve
    rewards = tuple(event for event in events if event.kind is EventKind.REWARD)
    if not rewards:
        return curve
    return replace(curve, slots=tuple(_rewarded(slot, rewards) for slot in curve.slots))


def _rewarded(slot: Slot, rewards: Sequence[Event]) -> Slot:
    """Return `slot` with every active reward added to its price (D1 §2)."""
    active = [
        amount
        for event in rewards
        if event.is_active_at(slot.start) and (amount := _per_kwh(event)) is not None
    ]
    extra = sum(active, Decimal(0))
    if not extra:
        return slot
    return replace(
        slot,
        total=slot.total + extra,
        components={**slot.components, "reward": extra},
    )


def _per_kwh(event: Event) -> Decimal | None:
    """Return a reward event's `per_kwh` payload, or `None` when it carries none."""
    value = event.payload.get("per_kwh")
    return None if value is None else Decimal(str(value))


@dataclass(frozen=True, slots=True)
class Curves:
    """Every curve the site plans against, by carrier and direction (D5 §4)."""

    import_: Mapping[Carrier, PriceCurve]
    export: Mapping[Carrier, PriceCurve] = field(default_factory=dict)

    def pair(self, carrier: Carrier = Carrier.ELECTRICITY) -> tuple[PriceCurve, PriceCurve | None]:
        """Return `(import, export)` for `carrier`; export is `None` where there is none."""
        return self.import_[carrier], self.export.get(carrier)

    def has(self, carrier: Carrier) -> bool:
        """Return whether this carrier is priced at all - a gas load may not be."""
        return carrier in self.import_


class Quantiser(Protocol):
    """How a load turns a grant in watts into what it will actually draw (D4 §4.2).

    D6 §5.3 quantises a modulating grant **down** through the device's own
    `ControlKind`, so the allocator is charged what the device will really take -
    the 6 A floor when a stop is vetoed, never the watts it asked for (INV-28,
    INV-39). D7 builds one per load from `Load.kind`; `LoadView.quantise` falls
    back to the demand's own floor when nothing was supplied.
    """

    def __call__(self, w: float, *, stop_ok: bool, session_active: bool) -> float:
        """Return the watts the device will draw if granted `w`."""
        ...


@dataclass(frozen=True, slots=True)
class LoadView:
    """One load as the planner sees it (D5 §4, D6 §4).

    A projection, not the `Load`: a strategy may read what the device is like and
    what it wants, and can reach neither its state nor its write gate. `demand`
    carries `max_w`, `min_w`, the requirement and the deadline, which is why they
    are properties here rather than copies that could drift from it.

    The last six fields are D6's (`design/DECISIONS.md` D-0160): the allocator asks
    what a load's draw does when it is granted (`thermostatic`), whether anything
    short of a blunt stage 4 may take it (`sheddable`), how long a grant sticks
    (`min_on_s`), which phases and how many it sits on (`phase_names`, `phases`),
    and how it quantises (`quantiser`). Each is load-shaped data only the load
    knows, and D5 ignores all of it.
    """

    load_id: str
    priority: int
    strategy: str
    demand: Demand
    mode: Mode = Mode.AUTO
    nameplate_w: float = 0.0
    kind: str = "modulate"
    carrier: Carrier = Carrier.ELECTRICITY
    min_block_min: int = 0
    participates_in_events: bool = False
    params: Mapping[str, Any] = field(default_factory=dict)
    store: StoreModel | None = None
    target: TargetProfile | None = None
    level_now: float | None = None
    #: The device modulates under its own control loop, so it reserves what it
    #: **measures** plus room to modulate up, not its rated power (D6 §5.2): an
    #: inverter at 23 W does not reserve 3 kW. Materialised by the device type.
    thermostatic: bool = False
    #: Whether anything below a blunt stage 4 may take this load (D6 §5.5): a
    #: heat pump is `False`, everything else `True`.
    sheddable: bool = True
    #: The kind's `min_on_s` - how long a grant holds before it may be taken
    #: back, unless the stage is 3 or above (D6 §5.3 step 5).
    min_on_s: float = 0.0
    #: Which phases the load sits on; `None` is unknown and gets the tightest
    #: phase's headroom (D3 §2, D6 §5.8).
    phase_names: frozenset[str] | None = None
    #: How many phases it is connected on, for `w_per_amp` (D3 §5.1).
    phases: Literal[1, 2, 3] = 1
    quantiser: Quantiser | None = None

    @property
    def max_w(self) -> float:
        """The most this load may draw now (D4's `Demand`)."""
        return self.demand.max_w

    @property
    def min_w(self) -> float:
        """The least it can run at - the 6 A cliff for a charger (INV-28)."""
        return self.demand.min_w

    def quantise(self, w: float, *, stop_ok: bool = False, session_active: bool = False) -> float:
        """Return what this load will draw if granted `w` (D6 §5.3 step 5).

        The device's own `ControlKind` when D7 supplied one; otherwise the floor
        rule that matters to the allocator's arithmetic: below the floor a
        modulating load either stops (authorised) or is held at the floor and
        charged for it, and it never draws a value between zero and its floor
        (INV-28, INV-39).
        """
        if self.quantiser is not None:
            return self.quantiser(w, stop_ok=stop_ok, session_active=session_active)
        floor = self.min_w
        if floor < 0.0:
            return w  # signed: a battery may be granted discharge
        if floor == 0.0 or w >= floor:
            return max(0.0, w)
        if stop_ok or not session_active:
            return 0.0
        return floor

    @property
    def forced(self) -> bool:
        """Whether the household has asked for this load to run now (D4 §5.2)."""
        return self.mode is Mode.FORCE

    @property
    def plans(self) -> bool:
        """Whether this load is planned at all: `off` and `delegated` are not (§5.1)."""
        return self.mode not in {Mode.OFF, Mode.DELEGATED}

    @classmethod
    def of(
        cls,
        load: Load,
        demand: Demand,
        *,
        mode: Mode = Mode.AUTO,
        level_now: float | None = None,
        quantiser: Quantiser | None = None,
    ) -> LoadView:
        """Return the view of `load` D7 hands the planner (D7 §5.2).

        `mode` is the **effective** mode - `Load.mode_now()`, with the site
        switch folded in (PLAN §7 dec. 20), because a site that is observing
        still plans and publishes (INV-44).

        `quantiser` is D6's (D-0160): D7 builds it from `load.kind` and the
        load's `KindCtx`, which only the engine has. Without one the view
        quantises on the demand's floor alone.
        """
        params = dict(load.config.strategy_params)
        materialised = load.config.params
        return cls(
            load_id=load.config.load_id,
            priority=load.config.priority,
            strategy=load.config.strategy,
            demand=demand,
            mode=mode,
            nameplate_w=load.config.nameplate_w,
            kind=load.kind.key,
            carrier=load.config.carrier,
            min_block_min=int(params.get("min_block_min", 0)),
            participates_in_events=bool(params.get("participate_in_events", False)),
            params=params,
            store=load.store,
            target=load.config.target,
            level_now=level_now,
            thermostatic=bool(
                materialised.get("thermostatic", materialised.get("kind") in ("setpoint", "mode"))
            ),
            sheddable=bool(materialised.get("sheddable", True)),
            min_on_s=load.kind.dwell_s()[0],
            phase_names=load.config.phase_names,
            phases=load.config.phases,
            quantiser=quantiser,
        )


@dataclass(frozen=True, slots=True)
class PlanContext:
    """Everything one strategy may read (D5 §4).

    Per load, built by `plan_all` from the `SiteContext` and the headroom left by
    the loads above it in priority - which is how a store comes to be charged
    before a demand window rather than during it (INV-31).
    """

    now: datetime
    tz: tzinfo
    curve_in: PriceCurve
    headroom: Headroom
    hysteresis: HysteresisPolicy
    load: LoadView
    curve_out: PriceCurve | None = None
    tariff_eligible: Sequence[tuple[datetime, datetime, float]] = ()
    presence: PresenceMode | None = None
    calendar: Sequence[CalendarEvent] = ()
    forecasts: Forecasts | None = None
    events: Sequence[Event] = ()
    horizon_h: float = 48.0
    holidays: HolidayCalendar = NO_HOLIDAYS
    previous: Plan | None = None

    @property
    def store(self) -> StoreModel | None:
        """The load's store model, where it has one (D4 §4.3)."""
        return self.load.store

    @property
    def level_now(self) -> float | None:
        """The store's level now - SoC in percent, temperature in °C, or unknown."""
        return self.load.level_now

    def horizon_end(self) -> datetime:
        """Return where planning stops: the horizon, bounded by the curve (D5 §2).

        D1 guarantees a full-horizon curve (INV-5), so this is normally
        `now + horizon_h`; a shorter curve bounds it, and never the other way
        round - a planner may not plan into slots that do not exist.
        """
        wanted = self.now + timedelta(hours=self.horizon_h)
        if not self.curve_in.slots:
            return self.now
        return min(wanted, self.curve_in.slots[-1].end)


@dataclass(frozen=True, slots=True)
class SiteContext:
    """What the whole planning cycle shares (D5 §5.1).

    `tariff` is the capacity axis; `None` is a `NoPeak` site, which degrades to
    pure price steering (HLD §3). `headroom` on `plan_all` overrides what this
    would build, which is how the backtest and the scenarios replay a window
    whose ceiling is already known.
    """

    tz: tzinfo
    hysteresis: HysteresisPolicy
    tariff: CeilingSource | None = None
    target: Target = AUTO
    presence: PresenceMode | None = None
    calendar: Sequence[CalendarEvent] = ()
    forecasts: Forecasts | None = None
    events: Sequence[Event] = ()
    horizon_h: float = 48.0
    holidays: HolidayCalendar = NO_HOLIDAYS
    stale: bool = False
    #: D3's ε for the window, in watts over the window, and the ladder's stage-2
    #: threshold: together the ceiling the plans are cut to (D5 §5.1, D-0257).
    eps_w: float = 0.0
    plan_fraction: float = 1.0


@dataclass(frozen=True, slots=True)
class SitePlan:
    """Every load's plan for this cycle, and what is left of the windows (§5.1, §5.12)."""

    plans: Mapping[str, Plan]
    headroom_left: Headroom
    adopted: frozenset[str] = frozenset()
    built_at: datetime | None = None

    @property
    def planned_kwh(self) -> float:
        """Total energy every plan intends to move."""
        return sum(plan.planned_kwh for plan in self.plans.values())

    @property
    def uncovered(self) -> tuple[str, ...]:
        """The loads whose requirement does not fit before their deadline (§8)."""
        return tuple(sorted(load_id for load_id, plan in self.plans.items() if not plan.covered))
