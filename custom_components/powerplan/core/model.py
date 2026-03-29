"""Types shared across the pure core (HLD §5).

Only the vocabulary HLD §2 and §5 name lives here - `Slot`, `PriceCurve`,
`Demand`, `Plan`, `Grant`, `Snapshot`, `Carrier`, `Confidence`, `Money`,
`Quality`. Everything else belongs to the domain that owns it.

Conventions (HLD §7.1–7.2): every `datetime` is tz-aware and keyed
in UTC; power is signed (import +, export −) and in watts; energy is kWh;
money is a `Decimal` in major units per kWh with the currency carried; slot
length is a property of the slot, never of the curve (INV-7).

WP0.1 ships the minimum each type needs to exist. Fields the owning LLD names
whose types belong to a domain not yet written are marked `deferred to WP…`
below and added by that WP, not invented here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

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


@dataclass(frozen=True, slots=True)
class PriceCurve:
    """One (carrier, direction) priced over a horizon (D1 §4).

    `slots` is sorted; gaps are allowed only in the past.
    """

    carrier: Carrier
    direction: Direction
    currency: str
    slots: tuple[Slot, ...]
    built_at: datetime
    sources: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Demand (D4) - what a load wants
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Demand:
    """What one load wants now and by when (HLD §2, D4 §4).

    `min_w` is negative for a battery or V2H (power is signed). `urgency:
    Urgency` and `comfort: ComfortState | None` are deferred to WP0.5, which
    is where those two vocabularies are defined.
    """

    wants: bool
    required_kwh: float | None
    deadline: datetime | None
    min_w: float
    max_w: float
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
