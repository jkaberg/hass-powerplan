"""The counterfactual: one shadow store per load (D11 §2, §5.3, INV-69).

HLD §10 decision 8 said "the same house with every load on `always` and no
capacity control". Read literally that is a second engine; implemented, it is the
load's **own** store model stepped once per closed slot under the policy its
uncontrolled thermostat, charger or programme would follow (PLAN §7 dec. 11).

Every parameter is the real load's *effective* value - configured, or learned
once D10's fit passed its gate. The shadow never has parameters of its own and
never fits one: if the model is wrong then so is the plan, and that shows
elsewhere (INV-63, INV-69). What the shadow honours is the household's intent -
the target profile, the presence mode, the plug-in, a `run_now`, a force. What it
does not honour is powerplan's plans, sheds and stages: that difference *is* the
savings.

A shadow is **not** tracked to the real level tick by tick - that would erase the
savings, because a slab pre-charged at night would show the counterfactual
"already warm" in the morning. It is re-anchored at a month rollover, at the end
of every observe slot (in observe the real trajectory *is* the counterfactual) and
when the level comes back after a day of blindness; and it is clamped to the
store's own bounds so it can never run away (D11 §5.3, §11).

Extension is a registry row: a new store model registers its shadow here or is
`NONE`, in which case cost is shown and savings are not stated. Every kind but
`NONE` has one since WP5.6 (`tank`, `schedule`, the battery's `idle`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol

from ...loads.base import CycleProfile
from ...loads.stores import CopCurve, DrawOffProfile, StoreModel
from ...model import Carrier, Demand, Mode

if TYPE_CHECKING:
    from ..close import ClosedSlot

__all__ = [
    "COUNTED_MODES",
    "LoadParams",
    "Shadow",
    "ShadowCtx",
    "ShadowState",
    "StoreKind",
    "anchored",
    "clamp_level",
    "kinds",
    "register",
    "shadow_for",
]


class StoreKind(StrEnum):
    """The store model a load banks in, and therefore its shadow (D11 §4)."""

    SLAB = "slab"
    ROOM = "room"
    HEAT_PUMP = "heat_pump"
    TANK = "tank"
    ENERGY = "energy"
    CYCLE = "cycle"
    SCHEDULE = "schedule"
    BATTERY = "battery"
    NONE = "none"


#: The effective modes whose slots carry a counterfactual (D11 §5.1 step 4).
#: `delegated` and `off` count cost and state no savings: powerplan is not
#: steering those slots, so there is nothing to have saved.
COUNTED_MODES: frozenset[Mode] = frozenset({Mode.AUTO, Mode.FORCE, Mode.OBSERVE})


@dataclass(frozen=True, slots=True)
class LoadParams:
    """The real load's effective parameters, as the shadow reads them (D11 §4).

    `loss_coeff_w_per_k` is the *effective* coefficient the planner uses - D10's
    learned value when its fit passed, else the configured or derived one. The
    shadow reads it and never fits it (INV-63).
    """

    kind: StoreKind
    nameplate_w: float = 0.0
    carrier: Carrier = Carrier.ELECTRICITY
    store: StoreModel | None = None
    band_k: float = 1.0
    hysteresis_k: float = 2.0
    loss_coeff_w_per_k: float | None = None
    cop: CopCurve | None = None
    rated_w: float | None = None
    #: The store's charge efficiency: an EV charger's, or a tank element's η
    #: (`TankStore.eta`, what the plug delivers that the water keeps - D-0380).
    charge_eff: float = 0.9
    max_w: float | None = None
    #: The cycle's own profile - duration, shape, energy - for `on_request`'s
    #: shadow. The type's default (D4 §6.8), never a live learned one: no
    #: shadow chases a live-updating parameter today (INV-63's promise is
    #: D10's, not yet wired to accounting for any kind).
    cycle_profile: CycleProfile | None = None
    #: The `tank` shadow's dial: the temperature a plain cylinder's own
    #: thermostat holds (`water_heater`'s `anchor_c`, D-0203, D-0380).
    charge_setpoint: float | None = None
    #: The tank's standing loss, W - `TankStore.standby_loss_w` (D4 §4.3).
    standby_loss_w: float = 0.0
    #: The household's hot water (D4 §5.7). The adapter reads it to give each
    #: slot its `ShadowCtx.draw_off_kwh`; the shadow reads only that.
    draw_off: DrawOffProfile | None = None
    #: The `schedule` shadow's daily quota: a relay load's own `hours_per_day`.
    hours_per_day: float | None = None


@dataclass(frozen=True, slots=True)
class ShadowCtx:
    """What the shadow of one load needs to know about this slot (D11 §4).

    `target` is the load's own target profile at slot start under the actual
    presence mode (D4 §5.8) - the household's intent, honoured. `level_now` is the
    measured level, used only for the three anchoring moments of §5.3, never to
    track the trajectory.
    """

    params: LoadParams
    mode: Mode = Mode.AUTO
    outdoor_c: float | None = None
    target: float | None = None
    demand: Demand | None = None
    level_now: float | None = None
    draw_off_kwh: float = 0.0
    legionella_active: bool = False
    #: What the real load drew in this slot (D3's `LoadSlot.kwh`), set by the
    #: ledger before stepping: the plug-in shadow reads the requirement **at the
    #: edge** as what is still asked at the slot's close plus what the slot already
    #: delivered (D-0269).
    measured_kwh: float = 0.0


@dataclass(frozen=True, slots=True)
class ShadowState:
    """One shadow's trajectory between slots (D11 §4, §7).

    Frozen and made of primitives: D7 writes it into the store section per closed
    slot, beside the ledger, so a restart resumes the counterfactual rather than
    restarting it at the real level.
    """

    kind: StoreKind
    anchored_at: datetime
    level: float | None = None
    on: bool = False
    pending_kwh: float = 0.0
    session_slots: tuple[str, ...] = ()
    #: The plug-in shadow's books (D-0269): what the car asked for at the last
    #: rising edge, at the slot's start, and the real kWh drawn since. A later
    #: edge re-latches only what the car has spent in between - a link that
    #: dropped and came back is not a new session.
    latched_kwh: float | None = None
    real_kwh: float = 0.0
    #: The on-request shadow's own run start (D11 §5.3, `cycle`), separate from
    #: `anchored_at` - the generic driver overwrites `anchored_at` on every
    #: re-anchor (month rollover, observe-slot end, return from blindness),
    #: which would otherwise silently reset an in-progress shadow run's clock.
    run_started_at: datetime | None = None


class Shadow(Protocol):
    """The counterfactual for one store model (D11 §3, §5.3)."""

    kind: ClassVar[StoreKind]

    def init(self, level_now: float | None, now: datetime, ctx: ShadowCtx) -> ShadowState:
        """Return a fresh trajectory anchored on the measured level."""
        ...

    def step(
        self, state: ShadowState, slot: ClosedSlot, ctx: ShadowCtx
    ) -> tuple[ShadowState, float]:
        """Return the state after this slot and the kWh it would have drawn."""
        ...

    def reanchor(self, state: ShadowState, level_now: float | None) -> ShadowState:
        """Return the trajectory pulled back onto the measured level (§5.3)."""
        ...


_REGISTRY: dict[StoreKind, Shadow] = {}


def register[S: Shadow](cls: type[S]) -> type[S]:
    """Register a shadow class under the store kind it models (D11 §3)."""
    _REGISTRY[cls.kind] = cls()
    return cls


def kinds() -> tuple[StoreKind, ...]:
    """Return every store kind that has a shadow, sorted."""
    return tuple(sorted(_REGISTRY))


def shadow_for(kind: StoreKind) -> Shadow | None:
    """Return the shadow for `kind`, or `None` when no baseline can be stated.

    `None` is a first-class answer, not a gap: a hydronic loop with no nameplate
    and a `delegated` device have no counterfactual anybody could defend, so their
    cost is shown and their savings are not (D11 §5.3).
    """
    return _REGISTRY.get(kind)


def clamp_level(level: float, store: StoreModel | None) -> float:
    """Hold a trajectory inside its store's own bounds (D11 §5.3).

    A shadow cannot run away: a thermostat that the model thinks never reaches its
    target would otherwise integrate a month of heating into one number.
    """
    if store is None:
        return level
    return min(max(level, store.min_level()), store.max_level())


def anchored(state: ShadowState, level_now: float | None, at: datetime) -> ShadowState:
    """Return `state` moved onto `level_now`, or unchanged when there is none."""
    if level_now is None:
        return state
    return replace(state, level=level_now, anchored_at=at)
