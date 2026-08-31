"""Fixtures for the D5 strategy tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite
(D9 §3). The price shapes come from `tests/builders/curves.py` - the
flat Norgespris day, the volatile NO3 day and the two DST days - and are turned
into a curve by `curve()` below rather than by D1's `build_curve`: what is under
test here is the planner, and a D5 test that failed because a modifier chain
changed would be a test of the wrong thing.

The loads are the two types that exist (D4): the reference house's charger and
one bathroom floor loop, built through the type registry so the `LoadView` a
strategy sees is the one D7 will hand it. Both are given `deadline_fill`
explicitly - `floor_heating.default_strategy` is `heat_capacitor`, which is
registered in WP3.2 - because what this WP ships is the fill, and a loop with a
step-up deadline is a fill (D5 §2).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from custom_components.powerplan.core.loads import (
    ConstantSchedule,
    LoadConfig,
    LoadCtx,
    LoadState,
    TargetProfile,
    build_load,
)
from custom_components.powerplan.core.loads.stores import EnergyStore, SlabStore
from custom_components.powerplan.core.metering import ElectricalProfile, VoltageSystem
from custom_components.powerplan.core.model import (
    Carrier,
    Confidence,
    Demand,
    Direction,
    Plan,
    PriceCurve,
    Slot,
    Urgency,
)
from custom_components.powerplan.core.pricing import HysteresisPolicy
from custom_components.powerplan.core.strategies import (
    Curves,
    Headroom,
    LoadView,
    PlanContext,
    SiteContext,
)
from tests.builders.curves import (
    ORDINARY,
    OSLO,
    day_bounds,
    flat_norgespris_day,
    volatile_no3_day,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from custom_components.powerplan.core.loads import Load, Reads
    from custom_components.powerplan.core.pricing import RawSlot

#: The reference house (D3 §5.1): 63 A, three-phase 230 V IT.
REFERENCE_PROFILE = ElectricalProfile(
    system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0, per_phase_limit_a=63.0
)

#: 230 V line-to-line on the IT system: a single-phase charger's W per amp.
W_PER_AMP = 230.0

#: An evening well away from `HH:00:00` (HLD §7.1), inside the ordinary day.
NOW = day_bounds(ORDINARY)[0] + timedelta(hours=21, minutes=7, seconds=13)

#: Departure the next morning - the EV's deadline (D4 §6.2).
DEPARTURE = day_bounds(ORDINARY)[1] + timedelta(hours=7)


# --------------------------------------------------------------------------- #
# Curves
# --------------------------------------------------------------------------- #


def curve(
    raw: Sequence[RawSlot],
    *,
    confidence: Confidence = Confidence.KNOWN,
    currency: str = "NOK",
) -> PriceCurve:
    """Return the raw day as a curve of one component and no modifiers."""
    slots = tuple(
        Slot(
            start=row.start,
            end=row.end,
            total=row.value,
            components={"spot": row.value},
            confidence=confidence,
        )
        for row in raw
    )
    return PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency=currency,
        slots=slots,
        built_at=slots[0].start,
        sources=("test",),
    )


def days_curve(
    *,
    flat: bool = False,
    first: date = ORDINARY,
    days: int = 2,
    minutes: int = 15,
    confidence: Confidence = Confidence.KNOWN,
) -> PriceCurve:
    """Return `days` consecutive local days - the horizon spans local midnight.

    `minutes` is the slot length: 15 by default, 60 where a test needs to show
    that the arithmetic reads the length off the slot (INV-7).
    """
    builder = flat_norgespris_day if flat else volatile_no3_day
    raw = tuple(
        row
        for offset in range(days)
        for row in builder(first + timedelta(days=offset), minutes=minutes)
    )
    return curve(raw, confidence=confidence)


def priced(base: PriceCurve, prices: Mapping[int, str]) -> PriceCurve:
    """Return `base` with the listed slot indices re-priced, for hand-built cases."""
    slots = list(base.slots)
    for index, value in prices.items():
        amount = Decimal(value)
        slots[index] = replace(slots[index], total=amount, components={"spot": amount})
    return replace(base, slots=tuple(slots))


def flat_curve(**kwargs: Any) -> PriceCurve:
    """Return flat Norgespris days (D1 §5.7)."""
    return days_curve(flat=True, **kwargs)


def volatile_curve(**kwargs: Any) -> PriceCurve:
    """Return volatile NO3 days, negative hour included (INV-51)."""
    return days_curve(flat=False, **kwargs)


# --------------------------------------------------------------------------- #
# Headroom
# --------------------------------------------------------------------------- #


def flat_headroom(source: PriceCurve, w: float = 10_000.0) -> Headroom:
    """Return a headroom of `w` in every slot of `source` - no tariff pressure."""
    return Headroom(by_slot={slot.start: w for slot in source.slots})


# --------------------------------------------------------------------------- #
# Loads
# --------------------------------------------------------------------------- #

#: What `ev.derive()` materialises for a 64 kWh car on a 32 A charger (D4 §6.2).
EV_PARAMS: Mapping[str, Any] = {
    "capacity_kwh": 64.0,
    "max_a": 32.0,
    "min_a": 6.0,
    "phases": 1,
    "target_soc": 80.0,
    "min_soc_now": 20.0,
    "charge_eff": 0.90,
    "step_up_a": 4.0,
    "settle_s": 60,
    "suppress_delta_a": 2.0,
    "suppress_stale_s": 60,
    "force_max_h": 6.0,
    "departures": {str(day): "07:00" for day in range(7)},
}

#: What `floor_heating.derive()` materialises for a bathroom loop (D4 §6.1).
FLOOR_PARAMS: Mapping[str, Any] = {
    "kind": "setpoint",
    "store": "slab",
    "comfort_c": 24.0,
    "floor_c": 21.0,
    "max_c": 27.0,
    "shed_setpoint_c": 21.0,
    "eco_setpoint_c": 22.0,
    "area_m2": 12.0,
    "screed_mm": 40.0,
    "w_per_m2": 80.0,
    "sensor": "floor",
    "swing_k": 1.0,
    "min_on_s": 900,
    "min_off_s": 900,
    "command_interval_s": 600,
    "substitutable": False,
    "follow_presence": True,
}


def ev_store() -> EnergyStore:
    """Return the reference house's 64 kWh car."""
    return EnergyStore(capacity_kwh=64.0, min_soc=20.0, max_soc=80.0, max_charge_w=7360.0)


def battery_store(**kwargs: Any) -> EnergyStore:
    """Return a 10 kWh home battery: LFP-shaped efficiencies, a 20 % reserve (D4 §6.6)."""
    options: dict[str, Any] = {
        "capacity_kwh": 10.0,
        "min_soc": 21.0,
        "max_soc": 100.0,
        "max_charge_w": 5000.0,
        "usable_fraction": 0.95,
        "charge_eff": 0.95,
        "discharge_eff": 0.95,
        "reserve_soc": 20.0,
        "max_discharge_w": 5000.0,
    }
    options.update(kwargs)
    return EnergyStore(**options)


def battery_view(**kwargs: Any) -> LoadView:
    """Return a home battery as the planner sees it (D5 §4, D4 §6.6)."""
    options: dict[str, Any] = {
        "load_id": "battery",
        "priority": 30,
        "strategy": "arbitrage",
        "demand": demand(
            wants=True,
            required_kwh=None,
            deadline=None,
            min_w=-5000.0,
            max_w=5000.0,
            urgency=Urgency.NORMAL,
            price_sensitive=True,
            reason="20 % of 100 %",
        ),
        "nameplate_w": 5000.0,
        "kind": "modulate",
        "store": battery_store(),
        "level_now": 50.0,
    }
    options.update(kwargs)
    return LoadView(**options)


def slab_store() -> SlabStore:
    """Return the bathroom slab: 12 m² under 40 mm of screed (D4 §6.1)."""
    return SlabStore(area_m2=12.0, screed_mm=40.0, loss_coeff_w_per_k=None, max_c=27.0)


def bathroom_target(**kwargs: Any) -> TargetProfile:
    """Return the bathroom loop's profile: comfort 24 °C, floor 21, cap 27."""
    options: dict[str, Any] = {
        "schedule": ConstantSchedule(24.0),
        "comfort_default": 24.0,
        "floor": 21.0,
        "ceiling": 27.0,
    }
    options.update(kwargs)
    return TargetProfile(**options)


def ev_load(**kwargs: Any) -> Load:
    """Build the charger through the type registry, as D7 will."""
    options: dict[str, Any] = {
        "load_id": "ev",
        "name": "Car charger",
        "type_key": "ev",
        "priority": 10,
        "nameplate_w": 32.0 * W_PER_AMP,
        "phases": 1,
        "params": dict(EV_PARAMS),
        "strategy": "deadline_fill",
    }
    options.update(kwargs)
    return build_load(LoadConfig(**options), ev_store())


def floor_load(**kwargs: Any) -> Load:
    """Build the bathroom loop through the type registry, as D7 will."""
    options: dict[str, Any] = {
        "load_id": "loop_bath",
        "name": "Bathroom floor",
        "type_key": "floor_heating",
        "priority": 32,
        "nameplate_w": 960.0,
        "phases": 1,
        "params": dict(FLOOR_PARAMS),
        "target": bathroom_target(),
        "strategy": "deadline_fill",
    }
    options.update(kwargs)
    return build_load(LoadConfig(**options), slab_store())


def demand(**kwargs: Any) -> Demand:
    """Return a `Demand`, defaulting to a car that wants 20 kWh by departure."""
    options: dict[str, Any] = {
        "wants": True,
        "required_kwh": 20.0,
        "deadline": DEPARTURE,
        "min_w": 6.0 * W_PER_AMP,
        "max_w": 32.0 * W_PER_AMP,
        "urgency": Urgency.DEADLINE,
        "comfort": None,
        "price_sensitive": True,
        "reason": "80 % by 07:00",
    }
    options.update(kwargs)
    return Demand(**options)


def ev_view(**kwargs: Any) -> LoadView:
    """Return the charger as the planner sees it (D5 §4)."""
    options: dict[str, Any] = {
        "load_id": "ev",
        "priority": 10,
        "strategy": "deadline_fill",
        "demand": demand(),
        "nameplate_w": 32.0 * W_PER_AMP,
        "kind": "modulate",
        "store": ev_store(),
        "level_now": 40.0,
    }
    options.update(kwargs)
    return LoadView(**options)


def floor_view(**kwargs: Any) -> LoadView:
    """Return the bathroom loop as the planner sees it (D5 §4)."""
    options: dict[str, Any] = {
        "load_id": "loop_bath",
        "priority": 32,
        "strategy": "deadline_fill",
        "demand": demand(
            required_kwh=3.0,
            deadline=None,
            min_w=0.0,
            max_w=960.0,
            urgency=Urgency.NORMAL,
            reason="24 °C",
        ),
        "nameplate_w": 960.0,
        "kind": "setpoint",
        "store": slab_store(),
        "target": bathroom_target(),
        "level_now": 22.0,
    }
    options.update(kwargs)
    return LoadView(**options)


def observed(load: Load, *, reads: Reads, now: datetime = NOW, **kwargs: Any) -> Demand:
    """Return what `load` asks for at `now` - the real `observe()`, not a mock."""
    ctx = LoadCtx(now=now, reads=reads, electrical=REFERENCE_PROFILE, zone=OSLO, **kwargs)
    _, observation = load.observe(LoadState(), ctx)
    return observation.demand


# --------------------------------------------------------------------------- #
# Forecasts (D10's surface, as D5 uses it)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Weather:
    """A constant forecast - D10's three questions with one answer each (D5 §4).

    Not a simulator: a forecast is data, not a device, and what is under test is
    what the planner does with a number rather than how the number is produced
    (D9 §2 keeps simulators for physical things). `outdoor_c = None` is the site
    that has no weather at all, which is a different answer from "mild".
    """

    outdoor: float | None = None
    surplus: float = 0.0
    baseline: float = 0.0
    #: A load's measured holding draw in W, by load id (D-0501).
    hold: dict[str, float] = field(default_factory=dict)

    def outdoor_c(self, t: datetime) -> float | None:
        """Return the forecast outdoor temperature."""
        return self.outdoor

    def surplus_w(self, t: datetime) -> float:
        """Return the PV surplus expected."""
        return self.surplus

    def baseline_w(self, t: datetime) -> float:
        """Return the uncontrolled load expected."""
        return self.baseline

    def hold_w(self, load_id: str, t: datetime) -> float | None:
        """Return the holding draw measured for `load_id` (D-0501): none unless a test sets it."""
        return self.hold.get(load_id)


# --------------------------------------------------------------------------- #
# Contexts
# --------------------------------------------------------------------------- #


def site_ctx(**kwargs: Any) -> SiteContext:
    """Return the site context `plan_all` walks (D5 §4, §5.1)."""
    options: dict[str, Any] = {"tz": OSLO, "hysteresis": HysteresisPolicy()}
    options.update(kwargs)
    return SiteContext(**options)


def plan_ctx(source: PriceCurve, **kwargs: Any) -> PlanContext:
    """Return a `PlanContext` over `source` with unlimited headroom (D5 §4)."""
    options: dict[str, Any] = {
        "now": NOW,
        "tz": OSLO,
        "curve_in": source,
        "headroom": flat_headroom(source),
        "hysteresis": HysteresisPolicy(),
        "load": ev_view(),
    }
    options.update(kwargs)
    return PlanContext(**options)


def curves_of(source: PriceCurve) -> Curves:
    """Return the one-carrier `Curves` a Norwegian site plans against."""
    return Curves(import_={Carrier.ELECTRICITY: source})


# --------------------------------------------------------------------------- #
# Shared assertions
# --------------------------------------------------------------------------- #


def filled(plan: Plan, *, after: datetime | None = None) -> tuple[datetime, ...]:
    """Return the starts of the slots the plan actually runs in, in time order."""
    return tuple(
        slot.start
        for slot in plan.slots
        if slot.envelope_w is not None
        and slot.envelope_w > 0.0
        and (after is None or slot.start >= after)
    )


def envelopes(plan: Plan) -> tuple[tuple[datetime, float | None], ...]:
    """Return every slot of the plan as `(start, envelope_w)` - the whole shape."""
    return tuple((slot.start, slot.envelope_w) for slot in plan.slots)
