"""Types shared across the pure core (HLD §5).

Only the vocabulary HLD §2 and §5 name lives here - `Slot`, `PriceCurve`,
`Demand`, `Plan`, `Grant`, `Snapshot`, `Carrier`, `Confidence`, `Money`,
`Quality` - plus the three D4 vocabularies a `Demand` is made of (`Mode`,
`Urgency`, `ComfortState`), which live here because `Demand` does and
`core/loads/` is below its own gate in the import graph (`design/DECISIONS.md`
D-0060). Everything else belongs to the domain that owns it.

Conventions (HLD §7.1–7.2): every `datetime` is tz-aware and keyed
in UTC; power is signed (import +, export −) and in watts; energy is kWh;
money is a `Decimal` in major units per kWh with the currency carried; slot
length is a property of the slot, never of the curve (INV-7).

WP0.1 ships the minimum each type needs to exist. Fields the owning LLD names
whose types belong to a domain not yet written are marked `deferred to WP…`
below and added by that WP, not invented here.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Final, Literal

# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #


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

    def price_at(self, t: datetime) -> Slot | None:
        """Return the slot containing `t`, or `None` outside the curve (D1 §5.8)."""
        for slot in self.slots:
            if slot.start > t:
                return None
            if t < slot.end:
                return slot
        return None

    def slots_between(self, a: datetime, b: datetime) -> tuple[Slot, ...]:
        """Return the slots overlapping `[a, b)` at their native lengths (INV-7)."""
        return tuple(slot for slot in self.slots if slot.end > a and slot.start < b)

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

    def coverage_h(self, from_: datetime) -> float:
        """Return the hours of `KNOWN` slots after `from_` (D1 §5.7).

        A planner must consult this and never plan past it: tomorrow's prices
        land around 13:00, so at 05:00 the horizon is ~19 h, not 48.
        """
        hours = 0.0
        for slot in self.slots:
            if slot.end <= from_ or slot.confidence is not Confidence.KNOWN:
                continue
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
    a vote.
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


# --------------------------------------------------------------------------- #
# Plan (D5) - when a load should run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PlanSlot:
    """One slot of a strategy's envelope for one load (HLD §6.5).

    `envelope_w`: `None` = no plan, control freely · `0.0` = stand still ·
    otherwise a cap in watts. `desired_state: SetpointDelta | Mode | None` is
    deferred to WP0.6, with the strategies that emit it.
    """

    start: datetime
    end: datetime
    envelope_w: float | None


@dataclass(frozen=True, slots=True)
class Plan:
    """A strategy's time-indexed envelope for one load (HLD §2, §6.5)."""

    slots: tuple[PlanSlot, ...]
    reason: str
    cost_estimate: Money
    coverage: float
    confidence: Confidence


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
    reasons: tuple[str, ...]
