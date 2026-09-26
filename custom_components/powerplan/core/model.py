"""Types shared across the pure core (HLD §5).

Only the vocabulary HLD §2 and §5 name lives here - `Slot`, `PriceCurve`,
`Demand`, `Plan`, `Grant`, `Snapshot`, `Carrier`, `Confidence`, `Money`,
`Quality` - plus the three D4 vocabularies a `Demand` is made of (`Mode`,
`Urgency`, `ComfortState`), which live here because `Demand` does and
`core/loads/` is below its own gate in the import graph (`design/DECISIONS.md`
D-0060), and `Desired`, which moved here in WP0.6 for the same reason: a
`PlanSlot` carries one and `core/model.py` is below `core/loads/`
(`design/DECISIONS.md` D-0131). Everything else belongs to the domain that owns
it.

Conventions (HLD §7.1–7.2): every `datetime` is tz-aware and keyed
in UTC; power is signed (import +, export −) and in watts; energy is kWh;
money is a `Decimal` in major units per kWh with the currency carried; slot
length is a property of the slot, never of the curve (INV-7).

WP0.1 ships the minimum each type needs to exist. Fields the owning LLD names
whose types belong to a domain not yet written are marked `deferred to WP…`
below and added by that WP, not invented here.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    # The Snapshot sections are domain types; importing them at runtime would
    # make the root module depend on D3/D6 (a cycle). String annotations on a
    # slotted dataclass are never evaluated, so these stay type-only (D-0235).
    from .allocation import AllocReport, Budget, LadderState  # noqa: TC004
    from .engine import (  # noqa: TC004
        AccountingStatus,
        ForecastStatus,
        HealthStatus,
        LoadStatus,
        PlanStatus,
        PriceStatus,
        SiteStatus,
        SiteWarning,
        TariffStatus,
    )
    from .metering import MeterSnapshot  # noqa: TC004

# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #


#: The last `datetime` turned into epoch seconds, and its value. A tick
#: asks the plans and the curves about the same `now` object thousands of times;
#: the pair is one tuple, swapped in one assignment, so a reader never sees a
#: date from one call beside the seconds of another.
_LAST_EPOCH: tuple[datetime, float] | None = None


def epoch(at: datetime) -> float:
    """Return `at.timestamp()`, reusing the answer for the object asked last."""
    global _LAST_EPOCH  # noqa: PLW0603 - a one-entry cache, replaced whole
    last = _LAST_EPOCH
    if last is not None and last[0] is at:
        return last[1]
    seconds = at.timestamp()
    _LAST_EPOCH = (at, seconds)
    return seconds


class Carrier(StrEnum):
    """What a price curve prices and a load consumes (HLD §2, D1 §4)."""

    ELECTRICITY = "electricity"
    GAS = "gas"
    DISTRICT_HEAT = "district_heat"
    OIL = "oil"
    PELLETS = "pellets"


class Direction(StrEnum):
    """Which way the energy flows past the meter (D1 §4).

    One curve per (carrier, direction); `PriceCurve` lives here, so this does
    too.
    """

    IMPORT = "import"
    EXPORT = "export"


class Confidence(StrEnum):
    """How much a value is worth trusting (INV-5, D1 §4).

    `SYNTHESISED` is the floor the last forecaster always reaches, so the
    planner knows night is cheaper than day even with every source dead.
    """

    KNOWN = "known"
    STALE = "stale"
    ESTIMATED = "estimated"
    SYNTHESISED = "synthesised"


class Mode(StrEnum):
    """What a load lets the controller do (HLD §6.4, D4 §5.2).

    `force` ignores price and keeps the ceiling; `observe` decides and
    publishes but never writes; `delegated` means someone else drives the
    device and powerplan only reserves its nameplate; `off` means the
    controller has let go - and let go loudly, with a `release()` first
    (INV-26). The effective mode folds the site switch in (PLAN §7 dec. 20,
    `core/loads/base.py`).
    """

    AUTO = "auto"
    FORCE = "force"
    OBSERVE = "observe"
    DELEGATED = "delegated"
    OFF = "off"


class Urgency(IntEnum):
    """How hard a load is asking (D4 §4.1).

    An `IntEnum` where the rest of the core's vocabularies are `StrEnum`s,
    because the order is the point: D5 and D6 both ask "is this at least a
    deadline?" and compare.
    """

    NONE = 0
    NORMAL = 1
    DEADLINE = 2
    LEGIONELLA = 3
    MIN_SOC = 4
    COMFORT_VIOLATION = 5


class Desired(StrEnum):
    """What the plan wants of a load this slot (D5 §2, consumed by D4 §5.4–5.5).

    A `MODE` load turns it into an option and a `SETPOINT` load into a delta.
    D5 produces it per slot; the allocator still owns the final word - a shed
    overrides `comfort`, and a comfort violation overrides `shed` (INV-1).
    """

    COMFORT = "comfort"
    SHED = "shed"


class PlanMode(StrEnum):
    """How a plan was built, and therefore what its cost means (D5 §4).

    `force` is "the household asked": the price was not consulted, so the cost is
    what this *will* cost rather than what it was optimised to, and a dashboard
    that presented the two the same way would lie about the one number the
    household judges the feature by (effektstyring `planner.plan`). `urgent` is
    a demand with no vote - a min-SoC floor, a legionella cycle, a comfort
    violation - and `none` is a requirement that could not be computed: both
    answer `cap_w = None`, which leaves the load to the allocator (D5 §8).
    """

    PRICE = "price"
    FORCE = "force"
    URGENT = "urgent"
    NONE = "none"


class PlanAnswer(StrEnum):
    """What a load's plan says about the slot a grant is for (INV-30, D6 §5.3).

    INV-30's three answers, carried past the allocator: `none` is no plan (control
    freely), `hold` a planned 0 (stand still) and `power` a planned cap. A grant
    the walk made without a plan's say - a comfort violator, a stage ≥ 1
    discharge, a frozen tick - carries no answer. A battery reads it to pick its
    command: `none` is the inverter's own self-use, `hold` keeps the charge (D4
    §4.2; PLAN §7 dec. 44).
    """

    NONE = "none"
    HOLD = "hold"
    POWER = "power"


class Quality(StrEnum):
    """The state of a reading taken from the outside world (D3 §4).

    Blindness is explicit: a stale or implausible reading freezes the tick and
    never opens a gate (INV-15, INV-17), and an unavailable bound entity
    degrades to `UNAVAILABLE` with a repair issue rather than raising
    (INV-53).
    """

    OK = "ok"
    STALE = "stale"
    IMPLAUSIBLE = "implausible"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in the major unit of a currency (HLD §7.2).

    `Decimal`, never `float`: the ledger (D11) sums these and a bill has to
    reconcile. `amount` may be negative - a paid-for hour is a real thing
    (INV-51).
    """

    amount: Decimal
    currency: str


# --------------------------------------------------------------------------- #
# Prices (D1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Slot:
    """One interval of a price curve (HLD §2, D1 §4).

    `total` is the sum of `components` in major units per kWh and MAY be
    negative; nothing in the pipeline clamps it (INV-51).
    """

    start: datetime
    end: datetime
    total: Decimal
    components: Mapping[str, Decimal]
    confidence: Confidence

    @property
    def minutes(self) -> int:
        """Return the slot's length in minutes (INV-7)."""
        return round((self.end - self.start).total_seconds() / 60)


#: Confidence from most to least trustworthy (INV-5). Blending slots of
#: different confidence keeps the last one reached.
_TRUST_ORDER: Final = (
    Confidence.KNOWN,
    Confidence.STALE,
    Confidence.ESTIMATED,
    Confidence.SYNTHESISED,
)


def _local_day_bounds(day: date, zone: tzinfo) -> tuple[datetime, datetime]:
    """Return local midnight and the next local midnight as instants (D1 §5.8).

    The difference is 23, 24 or 25 hours; nothing here assumes which.
    """
    return (
        datetime.combine(day, time.min, tzinfo=zone),
        datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone),
    )


def _blend(parts: tuple[Slot, ...], start: datetime, end: datetime) -> Slot:
    """Return one slot averaging `parts` over `[start, end)` by overlap (D1 §3)."""
    weights = [
        Decimal(int((min(slot.end, end) - max(slot.start, start)).total_seconds()))
        for slot in parts
    ]
    span = sum(weights, Decimal(0))
    keys = tuple(dict.fromkeys(key for slot in parts for key in slot.components))
    components = {
        key: sum(
            (
                slot.components.get(key, Decimal(0)) * weight
                for slot, weight in zip(parts, weights, strict=True)
            ),
            Decimal(0),
        )
        / span
        for key in keys
    }
    confidence = max((slot.confidence for slot in parts), key=_TRUST_ORDER.index)
    return Slot(
        start=start,
        end=end,
        total=sum(components.values(), Decimal(0)),
        components=components,
        confidence=confidence,
    )


@dataclass(frozen=True, slots=True)
class PriceCurve:
    """One (carrier, direction) priced over a horizon (D1 §4).

    `slots` is sorted; gaps are allowed only in the past. The statistics D1 §3
    lists live here, on the type, rather than beside the composition in
    `core/pricing/` (`design/DECISIONS.md` D-0030): D5 asks a curve what its
    spread is, and D2 and D11 price a bill from the same type without importing
    D1.
    """

    carrier: Carrier
    direction: Direction
    currency: str
    slots: tuple[Slot, ...]
    built_at: datetime
    sources: tuple[str, ...]
    #: Derived once from `slots`, never compared: every start for the bisections
    #: below, and the running hours of `KNOWN` slots for `coverage_h`. A curve
    #: is asked a few hundred times a tick; walking 400 slots each time was a
    #: third of the tick (`design/DECISIONS.md` D-0261).
    _starts: tuple[float, ...] = field(init=False, repr=False, compare=False, default=())
    _known_h: tuple[float, ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        """Index the sorted slots (D1 §4: `slots` is sorted, gaps only in the past).

        The keys are POSIX seconds: an aware `datetime` compares through its
        zone, and the bisections were a third of what the index had saved.
        """
        known: list[float] = []
        total = 0.0
        for slot in self.slots:
            if slot.confidence is Confidence.KNOWN:
                total += slot.minutes / 60.0
            known.append(total)
        object.__setattr__(self, "_starts", tuple(slot.start.timestamp() for slot in self.slots))
        object.__setattr__(self, "_known_h", tuple(known))

    def price_at(self, t: datetime) -> Slot | None:
        """Return the slot containing `t`, or `None` outside the curve (D1 §5.8)."""
        index = bisect_right(self._starts, epoch(t)) - 1
        if index < 0:
            return None
        slot = self.slots[index]
        return slot if t < slot.end else None

    def slots_between(self, a: datetime, b: datetime) -> tuple[Slot, ...]:
        """Return the slots overlapping `[a, b)` at their native lengths (INV-7)."""
        low = max(0, bisect_right(self._starts, epoch(a)) - 1)
        high = bisect_left(self._starts, epoch(b))
        return tuple(slot for slot in self.slots[low:high] if slot.end > a)

    def spread(self, day: date, zone: tzinfo) -> Decimal:
        """Return max − min `total` over the local day, 0 with no slots (D1 §5.7)."""
        totals = [slot.total for slot in self.slots_between(*_local_day_bounds(day, zone))]
        if not totals:
            return Decimal(0)
        return max(totals) - min(totals)

    def mean(self, day: date, zone: tzinfo) -> Decimal:
        """Return the duration-weighted mean `total` over the local day (D1 §5.7)."""
        slots = self.slots_between(*_local_day_bounds(day, zone))
        if not slots:
            return Decimal(0)
        weighted = sum((slot.total * slot.minutes for slot in slots), Decimal(0))
        return weighted / sum(slot.minutes for slot in slots)

    def is_flat(self, day: date, zone: tzinfo, threshold: Decimal) -> bool:
        """Return whether the local day's spread is under `threshold` (INV-8).

        The threshold comes from the `HysteresisPolicy` (D1 §5.7), never from
        an absolute number of minor units decided here.
        """
        return self.spread(day, zone) < threshold

    def has_known_after(self, t: datetime) -> bool:
        """Return whether a `KNOWN` slot starts at or after `t` (D7 §4.1 `tomorrow_available`)."""
        if not self.slots:
            return False
        index = bisect_left(self._starts, epoch(t))
        before = self._known_h[index - 1] if index > 0 else 0.0
        return self._known_h[-1] - before > 0.0

    def coverage_h(self, from_: datetime) -> float:
        """Return the hours of `KNOWN` slots after `from_` (D1 §5.7).

        A planner must consult this and never plan past it: tomorrow's prices
        land around 13:00, so at 05:00 the horizon is ~19 h, not 48.
        """
        if not self.slots:
            return 0.0
        index = bisect_right(self._starts, epoch(from_)) - 1
        hours = self._known_h[-1] - (self._known_h[index] if index >= 0 else 0.0)
        if index >= 0:
            slot = self.slots[index]
            if slot.confidence is Confidence.KNOWN and slot.end > from_:
                hours += (slot.end - max(slot.start, from_)).total_seconds() / 3600.0
        return hours

    def resample(self, minutes: int) -> PriceCurve:
        """Return the curve at one fixed resolution - **display only** (D1 §3).

        Strategies use native slot lengths (INV-7). Each output slot is the
        duration-weighted mean of the slots it covers and carries the least
        trusted confidence among them; a gap in the source stays a gap.
        """
        if not self.slots:
            return self
        step = timedelta(minutes=minutes)
        out: list[Slot] = []
        cursor = self.slots[0].start
        end = self.slots[-1].end
        while cursor < end:
            stop = min(cursor + step, end)
            parts = self.slots_between(cursor, stop)
            if parts:
                out.append(_blend(parts, cursor, stop))
            cursor = stop
        return replace(self, slots=tuple(out))


# --------------------------------------------------------------------------- #
# Demand (D4) - what a load wants
# --------------------------------------------------------------------------- #


#: Which way a thermal store works (D4 §4.1, §4.3). Heating and cooling are one
#: model with the sign flipped, so the direction is carried, never assumed.
type HeatDirection = Literal["heat", "cool"]


@dataclass(frozen=True, slots=True)
class ComfortState:
    """Where a load's comfort variable stands against its configuration (D4 §4.1).

    `target`, `floor` and `ceiling` come from configuration, never from the
    device: a thermostat in eco reports its eco setpoint, and reading that as
    the target closes a loop with no external cause (INV-27). `floor` is the
    one number no schedule and no presence mode may lower (INV-55).

    `deficit` is signed by `direction`: `target − current` when heating,
    `current − target` when cooling, so `≥ 0` always means "wants energy".
    """

    current: float | None
    target: float
    floor: float
    ceiling: float | None
    violated: bool
    deficit: float
    direction: HeatDirection = "heat"


@dataclass(frozen=True, slots=True)
class Demand:
    """What one load wants now and by when (HLD §2, D4 §4).

    `min_w` is negative for a battery or V2H (power is signed).
    `price_sensitive` is False under `force`, a min-SoC floor, a legionella
    cycle or a comfort violation - the four cases where the plan does not get
    a vote. `import_w` is the most of `max_w` that may come from the grid;
    `None` is all of it. A battery with grid charging off says 0.0, and D6 then
    grants it only the measured surplus (D6 §5.3, Phase 7; D-0209).
    """

    wants: bool
    required_kwh: float | None
    deadline: datetime | None
    min_w: float
    max_w: float
    urgency: Urgency
    comfort: ComfortState | None
    price_sensitive: bool
    reason: str
    import_w: float | None = None


# --------------------------------------------------------------------------- #
# Plan (D5) - when a load should run
# --------------------------------------------------------------------------- #


#: A setpoint delta in kelvin, relative to the configured comfort target - what
#: a `SETPOINT` load feels of a plan (D4 §5.4). A thermostat cannot be capped,
#: only re-targeted, so the delta is the lever and `envelope_w` the hint.
type SetpointDelta = float

#: What a plan may ask of a load beside its envelope (D5 §2, §4): an option for
#: a `MODE` load, a delta for a `SETPOINT` one.
type DesiredState = Desired | SetpointDelta


@dataclass(frozen=True, slots=True)
class PlanSlot:
    """One slot of a strategy's envelope for one load (HLD §6.5, D5 §4).

    `envelope_w`: `None` = no plan, control freely · `0.0` = stand still ·
    otherwise a cap in watts. The three are different answers and the
    distinction MUST survive every layer (INV-30); a planned `0.0` is never a
    shed (INV-25).

    `kwh` is the energy this slot was planned to move and `price` the effective
    price it was chosen at - kept per slot so the plan can be priced, published
    and compared without the curve it came from. `hold_kwh` is what a thermal
    store draws holding its setpoint in the slot, the thermostat's own business:
    priced and projected, but never counted as moving the store towards a need
    (D5 §5.7, `design/DECISIONS.md` D-0501). `committed` is set by the
    builder: a slot that has started, or is `KNOWN` and starts inside the
    commitment window, moves only for twice the hysteresis (D5 §5.9).
    """

    start: datetime
    end: datetime
    envelope_w: float | None
    desired_state: DesiredState | None = None
    kwh: float = 0.0
    price: Decimal = Decimal(0)
    reason: str = ""
    committed: bool = False
    hold_kwh: float = 0.0
    #: Phase 7 (D5 §2, D6 §5.3): how much of the slot's planned draw comes from the
    #: forecast PV surplus, W. The rest is `grid_w`, the most the plan imports.
    surplus_w: float = 0.0

    @property
    def hours(self) -> float:
        """Return the slot's length in hours - from the slot, never a constant."""
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def grid_w(self) -> float | None:
        """Return what the plan imports in this slot: the envelope less its surplus (D6 §5.3)."""
        if self.envelope_w is None:
            return None
        return max(0.0, self.envelope_w - self.surplus_w)

    @property
    def planned_draw_w(self) -> float:
        """Return the mean watts this slot plans to draw, bounded by its envelope (D-0629).

        The envelope where a strategy cut the slot to its energy; less where the
        envelope is a cap the load runs free under - a banked or held thermostat -
        whose standing loss is all it takes. A slot told to stand still, or one that
        discharges, draws nothing. D5 reserves it for the loads below (D5 §5.1) and
        D6 holds it back for an idle thermostat (D6 §5.2, D-0686).
        """
        if self.envelope_w is not None and self.envelope_w <= 0.0:
            return 0.0
        draw = (self.kwh + self.hold_kwh) / self.hours * 1000.0
        return draw if self.envelope_w is None else min(draw, self.envelope_w)

    def contains(self, t: datetime) -> bool:
        """Return whether `t` falls in `[start, end)`."""
        return self.start <= t < self.end


@dataclass(frozen=True, slots=True)
class Plan:
    """A strategy's time-indexed envelope for one load (HLD §2, §6.5, D5 §4).

    The questions D6, D7 and D8 ask a plan are methods on the type rather than
    functions in `core/strategies/` - the same choice, for the same reason, as
    the curve statistics on `PriceCurve` (`design/DECISIONS.md` D-0030, D-0130):
    the allocator asks a plan what it may grant without importing D5.
    """

    load_id: str
    strategy: str
    mode: PlanMode
    slots: tuple[PlanSlot, ...]
    built_at: datetime
    cost_estimate: Money
    confidence: Confidence
    reason: str = ""
    required_kwh: float | None = None
    planned_kwh: float = 0.0
    #: What holding the store's setpoint draws over the plan (D-0501); not in `planned_kwh`.
    hold_kwh: float = 0.0
    covered: bool = True
    coverage: float = 1.0
    deadline: datetime | None = None
    inputs_hash: str = ""
    #: Derived once from `slots` (time order, D5 §4), never compared (D-0261):
    #: every start, and the active slots' (`envelope_w > 0`) starts and ends for
    #: the stop horizons D6 asks about every tick.
    _starts: tuple[float, ...] = field(init=False, repr=False, compare=False, default=())
    _active_starts: tuple[datetime, ...] = field(init=False, repr=False, compare=False, default=())
    _active_ends: tuple[float, ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        """Index the slots for the lookups every tick makes."""
        active = [
            slot for slot in self.slots if slot.envelope_w is not None and slot.envelope_w > 0.0
        ]
        object.__setattr__(self, "_starts", tuple(slot.start.timestamp() for slot in self.slots))
        object.__setattr__(self, "_active_starts", tuple(slot.start for slot in active))
        object.__setattr__(self, "_active_ends", tuple(slot.end.timestamp() for slot in active))

    def slots_between(self, a: datetime, b: datetime) -> tuple[PlanSlot, ...]:
        """Return the slots overlapping `[a, b)`."""
        low = max(0, bisect_right(self._starts, epoch(a)) - 1)
        high = bisect_left(self._starts, epoch(b))
        return tuple(slot for slot in self.slots[low:high] if slot.end > a)

    def slot_at(self, now: datetime) -> PlanSlot | None:
        """Return the slot containing `now`, or `None` outside the plan."""
        index = bisect_right(self._starts, epoch(now)) - 1
        if index < 0:
            return None
        slot = self.slots[index]
        return slot if now < slot.end else None

    def cap_w(self, now: datetime) -> float | None:
        """Return what this load may draw now: `None` free · `0` idle · `w` cap.

        `None` for a plan that has nothing to say - no requirement, an urgent
        demand with no vote, a load with no price steering - and for a slot the
        strategy left free. A plan that exists but does not run in this slot
        answers `0.0`: it stands still, and standing still is not a shed
        (INV-25, INV-30).
        """
        if self.mode is PlanMode.NONE or not self.slots:
            return None
        slot = self.slot_at(now)
        # Outside every slot the plan has nothing to say: `None`, never a hold
        # (INV-30, D-0688). Adoption replaces such a plan before it gets here.
        return None if slot is None else slot.envelope_w

    def desired_state_at(self, now: datetime) -> DesiredState | None:
        """Return the option or delta the plan wants of the device now (D5 §2)."""
        slot = self.slot_at(now)
        return None if slot is None else slot.desired_state

    def idle_seconds_from(self, now: datetime, horizon_s: float = 3600.0) -> float:
        """Return how long the plan keeps this load at zero from `now` (D5 §5.10).

        The horizon of a *plan* stop, and the answer to "is stopping the charger
        worth it": ending a session costs about ten minutes, so a stop that will
        be reversed in ninety seconds is a straight loss (INV-39). `horizon_s`
        when the plan draws no more, or has nothing to say - then the caller
        decides on other grounds.
        """
        index = bisect_right(self._active_ends, epoch(now))
        if index >= len(self._active_starts):
            return horizon_s
        start = self._active_starts[index]
        if start <= now:
            return 0.0
        return min(horizon_s, max(0.0, (start - now).total_seconds()))

    def next_active(self, now: datetime) -> datetime | None:
        """Return when the plan next draws power, `now` if it already does.

        D6 combines it with the window's remaining time for a *budget* stop's
        horizon - undone by the window turning or by the plan (INV-39).
        """
        index = bisect_right(self._active_ends, epoch(now))
        if index >= len(self._active_starts):
            return None
        return max(self._active_starts[index], now)


# --------------------------------------------------------------------------- #
# Grant (D6) - the allocator's decision
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Grant:
    """The allocator's decision for one load this tick (HLD §2, D6 §4).

    A zero `w` is not a shed (INV-25): `shed` and `shed_reason` say so
    explicitly, and only the allocator may authorise a stop (`stop_ok`,
    INV-39). `blunt` marks a stage-4 reason that is physical or contractual,
    never a capacity step (INV-36).
    """

    w: float
    shed: bool
    shed_reason: str | None
    stop_ok: bool
    stage: int
    blunt: bool
    capped_by: tuple[str, ...]
    #: What the plan said about this slot, where it had a say (INV-30, D6 §5.3).
    answer: PlanAnswer | None = None


# --------------------------------------------------------------------------- #
# Snapshot (D7) - the immutable result of one tick
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What one tick decided: the contract with D8 and D9 (HLD §2, D7 §4.1).

    The per-domain sections - `site`, `meter`, `budget`, `ladder`, `tariff`,
    `prices`, `plans`, `loads`, `alloc`, `forecasts`, `accounting`,
    `warnings` - are added by the WP that defines each one, ending with WP0.8
    (D7 §4.1). `reasons` is the human-readable trail, ≤ 20 lines, and is
    published on every tick even when the site is off or observing (INV-44).
    """

    schema: int
    at: datetime
    tick_no: int
    duration_ms: float
    site: SiteStatus
    meter: MeterSnapshot | None
    budget: Budget | None
    ladder: LadderState
    tariff: TariffStatus | None
    prices: PriceStatus
    plans: Mapping[str, PlanStatus]
    loads: Mapping[str, LoadStatus]
    alloc: AllocReport
    forecasts: ForecastStatus
    accounting: AccountingStatus
    warnings: tuple[SiteWarning, ...]
    health: HealthStatus
    reasons: tuple[str, ...]
    #: What the household should expect of the window in progress: `used`, the
    #: uncontrolled term for what is left of it and every no-vote demand - D7
    #: §5.4's `expected`, never a plan's energy. Published beside the ladder's
    #: projection and never read by it (D-0685).
    expected_kwh: float | None = None
