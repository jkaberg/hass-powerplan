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
from ..tariffs import AUTO, PricedLimit, Target, TimeFilter

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
    "effective_curve",
    "surplus_bands",
    "with_rewards",
]


class CeilingSource(Protocol):
    """The two questions the headroom builder asks a tariff (D2 §3, D5 §5.1).

    A narrow view of D2's `TariffEvaluator`: `Evaluator` satisfies it structurally,
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

    def priced_limit_now(self, now: datetime) -> PricedLimit | None:
        """Return a limit whose excess is priced, in force at `now` (D2 §5.8, O23)."""
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

    def hold_w(self, load_id: str, t: datetime) -> float | None:
        """Return what a thermal load draws to hold its temperature at `t`, measured (D-0501)."""
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
    #: v0.2.2 (O23): the watts left under a priced limit per slot, and what a kWh above
    #: it costs on top - D5 §5.1's power tier. Empty where no priced limit is in force.
    tier: Mapping[datetime, float] = field(default_factory=dict)
    surcharge: Mapping[datetime, Decimal] = field(default_factory=dict)

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
        tier: dict[datetime, float] = {}
        surcharge: dict[datetime, Decimal] = {}
        for slot in slots:
            ceiling = math.inf if tariff is None else tariff.target_w_at(slot.start, target)
            baseline = 0.0 if forecasts is None else forecasts.baseline_w(slot.start)
            priced = None if tariff is None else tariff.priced_limit_now(slot.start)
            if priced is not None and priced.per_kwh is not None:
                tier[slot.start] = max(0.0, priced.w - baseline)
                surcharge[slot.start] = priced.per_kwh.amount
            if math.isinf(ceiling):
                room[slot.start] = ceiling
                continue
            guarded = max(0.0, ceiling - eps_w) * fraction
            room[slot.start] = max(0.0, guarded - baseline)
        return cls(by_slot=room, tier=tier, surcharge=surcharge)

    def w_at(self, start: datetime) -> float:
        """Return the watts available in the slot starting at `start`."""
        return self.by_slot.get(start, math.inf)

    def reserve(self, taken: Mapping[datetime, float]) -> Headroom:
        """Return the headroom left after `taken` watts are reserved per slot."""
        if not taken:
            return self
        room = dict(self.by_slot)
        tier = dict(self.tier)
        for start, watts in taken.items():
            room[start] = max(0.0, self.w_at(start) - watts)
            if start in tier:
                tier[start] = max(0.0, tier[start] - watts)
        return replace(self, by_slot=room, tier=tier)

    def closed(self, starts: set[datetime]) -> Headroom:
        """Return this headroom with no watts in `starts` - a switched load's closed slots (G14)."""
        if not starts:
            return self
        room = dict(self.by_slot)
        tier = dict(self.tier)
        for start in starts:
            room[start] = 0.0
            if start in tier:
                tier[start] = 0.0
        return replace(self, by_slot=room, tier=tier)

    def tier_at(self, start: datetime) -> tuple[float, Decimal] | None:
        """Return the watts left under a priced limit and its surcharge, or `None`."""
        if start not in self.tier:
            return None
        return self.tier[start], self.surcharge[start]


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


def surplus_bands(
    surplus_w: float, *, export_limit_w: float | None, p_in: Decimal, p_out: Decimal
) -> tuple[tuple[float, Decimal], ...]:
    """Return a slot's surplus as `(watts, price)` bands, cheapest first (D5 §2, Phase 7).

    Surplus above the site's export limit could not be sold, so it costs nothing;
    the rest costs the export it forgoes, `p_out`. Both are capped at `p_in`, so the
    bands and the grid after them rise with the watts: a slot's energy is then
    convex in its watts and the greedy over `(slot, band)` pairs stays exact
    (§5.2) - and a load does draw the surplus first, whatever it is worth.
    """
    if surplus_w <= 0.0:
        return ()
    stranded = 0.0 if export_limit_w is None else max(0.0, surplus_w - export_limit_w)
    sold = surplus_w - stranded
    forgone = min(p_out, p_in)
    bands: list[tuple[float, Decimal]] = []
    if stranded > 0.0:
        bands.append((stranded, min(Decimal(0), forgone)))
    if sold > 0.0:
        bands.append((sold, forgone))
    return tuple(bands)


def _band_price(bands: Sequence[tuple[float, Decimal]], p_in: Decimal, w: float) -> Decimal:
    """Return the mean price of `w` watts drawn through `bands`, then the grid at `p_in`."""
    if w <= 0.0:
        return p_in
    left = w
    cost = Decimal(0)
    for watts, price in bands:
        take = min(left, watts)
        cost += price * Decimal(str(take))
        left -= take
        if left <= 0.0:
            break
    if left > 0.0:
        cost += p_in * Decimal(str(left))
    return cost / Decimal(str(w))


def effective_curve(
    curve_in: PriceCurve,
    curve_out: PriceCurve | None,
    surplus: Mapping[datetime, float],
    *,
    export_limit_w: float | None,
    draw_w: float,
) -> PriceCurve:
    """Return `curve_in` priced as `draw_w` watts would cost with the forecast surplus (D5 §2).

    The curve a strategy that ranks whole slots plans on: each slot's total is the
    mean of the surplus bands and the grid over the load's draw, and the
    difference is its `surplus` component. Without surplus the curve is returned
    as it is - the same object, so a site without panels plans bit for bit as it
    did (§9 17).
    """
    if not any(watts > 0.0 for watts in surplus.values()):
        return curve_in
    slots: list[Slot] = []
    for slot in curve_in.slots:
        watts = surplus.get(slot.start, 0.0)
        if watts <= 0.0:
            slots.append(slot)
            continue
        bands = surplus_bands(
            watts,
            export_limit_w=export_limit_w,
            p_in=slot.total,
            p_out=_export_price(curve_out, slot.start),
        )
        price = _band_price(bands, slot.total, draw_w)
        slots.append(
            replace(
                slot,
                total=price,
                components={**slot.components, "surplus": price - slot.total},
            )
        )
    return replace(curve_in, slots=tuple(slots))


def _export_price(curve_out: PriceCurve | None, start: datetime) -> Decimal:
    """Return what a kWh exported in the slot at `start` earns: 0 without an export curve."""
    if curve_out is None:
        return Decimal(0)
    slot = curve_out.price_at(start)
    return Decimal(0) if slot is None else slot.total


@dataclass(frozen=True, slots=True)
class Curves:
    """Every curve the site plans against, by carrier and direction (D5 §4)."""

    import_: Mapping[Carrier, PriceCurve]
    export: Mapping[Carrier, PriceCurve] = field(default_factory=dict)
    #: v0.2.2 (G13): the import curve of each grid tariff on one load's meter, by its key.
    per_load: Mapping[str, PriceCurve] = field(default_factory=dict)

    def pair(self, carrier: Carrier = Carrier.ELECTRICITY) -> tuple[PriceCurve, PriceCurve | None]:
        """Return `(import, export)` for `carrier`; export is `None` where there is none."""
        return self.import_[carrier], self.export.get(carrier)

    def for_load(self, view: LoadView) -> tuple[PriceCurve, PriceCurve | None]:
        """Return `(import, export)` a load plans on: its own tariff's curve where it has one."""
        curve_in, curve_out = self.pair(view.carrier)
        if view.grid_tariff is not None and view.grid_tariff in self.per_load:
            return self.per_load[view.grid_tariff], curve_out
        return curve_in, curve_out

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
    #: A relay behind a thermostat - a floor's mode select, a tank's setpoint:
    #: it draws its nameplate or nothing, and whether it closes is the device's
    #: call. Idle, it reserves its plan's draw for the slot, not a margin (D6
    #: §5.2, D-0686). A heat pump modulates and is `thermostatic` alone.
    relay_thermostat: bool = False
    #: This slot's planned draw, `PlanSlot.planned_draw_w`, stamped by the tick
    #: for the allocator; 0 with no plan or outside it.
    planned_draw_w: float = 0.0
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
    #: The load's own grid tariff (D4 §5.16, G13): planned on `Curves.per_load`.
    grid_tariff: str | None = None
    #: The windows the grid switches it in (G14): nothing planned or granted
    #: outside them. `None` is a load the grid does not switch.
    allowed: tuple[TimeFilter, ...] | None = None

    @property
    def max_w(self) -> float:
        """The most this load may draw now (D4's `Demand`)."""
        return self.demand.max_w

    @property
    def min_w(self) -> float:
        """The least it can run at - the 6 A cliff for a charger (INV-28)."""
        return self.demand.min_w

    def allowed_at(self, when: datetime, tz: tzinfo, calendar: HolidayCalendar) -> bool:
        """Whether the grid lets this load draw at `when` (G14); always, where it does not switch."""
        if self.allowed is None:
            return True
        return any(window.matches(when, tz, calendar) for window in self.allowed)

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
        static, params = _static_fields(load)
        return cls(
            demand=demand,
            mode=mode,
            level_now=level_now,
            quantiser=quantiser,
            params=dict(params),
            **static,
        )


#: The fields of a `LoadView` that depend on the load alone, per load object -
#: the planner and the allocator ask for a view of every load every tick, and
#: none of these can change without a new `Load`. The load itself is
#: kept in the entry, so its `id` cannot be reused while the entry lives.
_STATIC: dict[int, tuple[Load, dict[str, Any], dict[str, Any]]] = {}


def _static_fields(load: Load) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the view's load-only fields and, apart, the params each view copies."""
    hit = _STATIC.get(id(load))
    if hit is not None and hit[0] is load:
        return hit[1], hit[2]
    params = dict(load.config.strategy_params)
    materialised = load.config.params
    static: dict[str, Any] = {
        "load_id": load.config.load_id,
        "priority": load.config.priority,
        "strategy": load.config.strategy,
        "nameplate_w": load.config.nameplate_w,
        "kind": load.kind.key,
        "carrier": load.config.carrier,
        "min_block_min": int(params.get("min_block_min", 0)),
        "participates_in_events": bool(params.get("participate_in_events", False)),
        "store": load.store,
        "target": load.config.target,
        "thermostatic": bool(
            materialised.get("thermostatic", materialised.get("kind") in ("setpoint", "mode"))
        ),
        "relay_thermostat": bool(
            materialised.get(
                "relay_thermostat",
                materialised.get("kind") in ("setpoint", "mode")
                and load.config.type_key != "heat_pump",
            )
        ),
        "sheddable": bool(materialised.get("sheddable", True)),
        "min_on_s": load.kind.dwell_s()[0],
        "phase_names": load.config.phase_names,
        "phases": load.config.phases,
        "grid_tariff": load.config.grid_tariff,
        "allowed": load.config.allowed,
    }
    _STATIC[id(load)] = (load, static, params)
    return static, params


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
    #: D3 §4: the most the site may export, in W; `None` is the fuse. Never on
    #: the capacity axis, which counts import only (INV-19, D3 §9 21).
    export_limit_w: float | None = None
    #: D5 §2 (Phase 7): the forecast PV surplus left per slot start, W, after the
    #: loads above this one took theirs (INV-33's walk). Empty without panels.
    surplus: Mapping[datetime, float] = field(default_factory=dict)
    #: `curve_in` priced at this load's draw with that surplus (`effective_curve`);
    #: `None` where there is no surplus, and `effective` is then `curve_in`.
    curve_eff: PriceCurve | None = None

    @property
    def effective(self) -> PriceCurve:
        """The curve a strategy ranks whole slots on (D5 §2): `curve_in` without surplus."""
        return self.curve_in if self.curve_eff is None else self.curve_eff

    def surplus_bands(self, slot: Slot) -> tuple[tuple[float, Decimal], ...]:
        """Return this slot's surplus left as `(watts, price)` bands, cheapest first (D5 §2)."""
        return surplus_bands(
            self.surplus.get(slot.start, 0.0),
            export_limit_w=self.export_limit_w,
            p_in=slot.total,
            p_out=_export_price(self.curve_out, slot.start),
        )

    def effective_price(self, slot: Slot, w: float) -> Decimal:
        """Return the mean price of `w` watts in `slot`: surplus bands first, then the grid."""
        return _band_price(self.surplus_bands(slot), slot.total, w)

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
    #: D3 §4's export cap, handed to each `PlanContext` unchanged (D3 §9 21).
    export_limit_w: float | None = None


@dataclass(frozen=True, slots=True)
class SitePlan:
    """Every load's plan for this cycle, and what is left of the windows (§5.1, §5.12)."""

    plans: Mapping[str, Plan]
    headroom_left: Headroom
    adopted: frozenset[str] = frozenset()
    built_at: datetime | None = None
    #: The adopted loads whose kept plan no longer fit its room (D-0628): a forced re-cut.
    refit: frozenset[str] = frozenset()

    @property
    def planned_kwh(self) -> float:
        """Total energy every plan intends to move."""
        return sum(plan.planned_kwh for plan in self.plans.values())

    @property
    def uncovered(self) -> tuple[str, ...]:
        """The loads whose requirement does not fit before their deadline (§8)."""
        return tuple(sorted(load_id for load_id, plan in self.plans.items() if not plan.covered))
