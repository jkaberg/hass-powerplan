"""Fixtures for the D6 allocation tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite
(D9 §3). What D6 allocates against is **numbers** - a budget, a
reservation, a per-phase headroom - so the fixtures here are builders for the
five inputs one tick takes: the `MeterSnapshot` D3 hands it, D2's `Ceiling`, the
`LoadView`s D5 planned, their `Plan`s, and the `ControlledView` each load's meter
row carries.

The loads are the reference house's: a 32 A single-phase charger (priority 10), a
3 kW water tank on a relay (20), a 960 W bathroom loop on a thermostat (32) and a
3 kW inverter heat pump (50, `thermostatic`, not `sheddable`). Their control kinds
are the **real** D4 kinds, not stand-ins: the charger's quantiser is
`Modulate(ModulateCfg(...))`, so the 6 A cliff in `tests/core/allocation`
is the same cliff `apply()` will hit (INV-28, D6 §9 23).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.allocation import (
    AllocCtx,
    AllocState,
    Budget,
    BudgetCfg,
    HardLimits,
    PiState,
)
from custom_components.powerplan.core.allocation import (
    budget as build_budget,
)
from custom_components.powerplan.core.loads.kinds import Modulate, ModulateCfg
from custom_components.powerplan.core.loads.kinds.base import KindCtx, Reads
from custom_components.powerplan.core.metering import (
    AnchorKind,
    ControlledView,
    ElectricalProfile,
    MeterHealth,
    MeterSnapshot,
    PhaseReadings,
    VoltageSystem,
    window_bounds,
)
from custom_components.powerplan.core.model import (
    Carrier,
    ComfortState,
    Confidence,
    Demand,
    Direction,
    Grant,
    Money,
    Plan,
    PlanMode,
    PlanSlot,
    PriceCurve,
    Quality,
    Slot,
    Urgency,
)
from custom_components.powerplan.core.strategies import LoadView
from custom_components.powerplan.core.tariffs import Ceiling

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from custom_components.powerplan.core.allocation import Constraint

OSLO = ZoneInfo("Europe/Oslo")

#: The reference house (D3 §5.1): 63 A, three-phase 230 V IT, ≈ 25.1 kW.
REFERENCE_PROFILE = ElectricalProfile(
    system=VoltageSystem.IT_230, phases=3, main_fuse_a=63.0, per_phase_limit_a=63.0
)

#: 230 V line-to-line on the IT system: a single-phase load's W per amp.
W_PER_AMP = 230.0

#: A cold February evening, well away from `HH:00:00` (HLD §7.1).:07:13 into a
#: 60-minute window leaves 52 min 47 s, which is 0.8797 h.
NOW = datetime(2026, 2, 3, 18, 7, 13, tzinfo=OSLO)

#: The house's main fuse in watts: 63 A × √3 × 230 V.
FUSE_W = REFERENCE_PROFILE.fuse_w()


# --------------------------------------------------------------------------- #
# The meter
# --------------------------------------------------------------------------- #


def meter(
    *,
    now: datetime = NOW,
    window_min: int = 60,
    t_rem_h: float | None = None,
    used_kwh: float = 0.0,
    grid_w: float = 0.0,
    grid_smooth_w: float | None = None,
    sigma_w: float | None = 500.0,
    uncontrolled_w: float = 0.0,
    seam: bool = False,
    stale: bool = False,
    degraded: bool = False,
    phases: PhaseReadings | None = None,
    closed: Sequence[Any] = (),
) -> MeterSnapshot:
    """Return the `MeterSnapshot` D3 hands one tick (D3 §4).

    `t_rem_h` defaults to what the clock says for `now`; passing it is how a test
    puts itself at:05 or:55 of the same window without moving the clock.
    `uncontrolled_w` defaults to zero - the walk's arithmetic is then about the
    allowance alone, and the tests that care about the baseline pass it.
    """
    start, end = window_bounds(now, window_min, OSLO)
    elapsed = (now - start).total_seconds() / 3600.0
    remaining = (end - now).total_seconds() / 3600.0 if t_rem_h is None else t_rem_h
    frozen_reason = "stale" if stale else ("seam" if seam else None)
    return MeterSnapshot(
        now=now,
        window_start_utc=start,
        window_min=window_min,
        t_elapsed_h=elapsed,
        t_rem_h=remaining,
        seam=seam,
        frozen_reason=frozen_reason,
        used_kwh=used_kwh,
        used_confidence="estimated" if degraded else "exact",
        grid_w=grid_w,
        grid_smooth_w=grid_w if grid_smooth_w is None else grid_smooth_w,
        import_w=max(0.0, grid_w),
        export_w=max(0.0, -grid_w),
        production_w=None,
        consumption_w=max(0.0, grid_w),
        consumption_quality=Quality.OK,
        surplus_w=max(0.0, -grid_w),
        uncontrolled_w=uncontrolled_w,
        sigma_uncontrolled_w=sigma_w,
        sigma_samples=120,
        phases=phases,
        closed=tuple(closed),
        health=MeterHealth(
            power_age_s=2.0,
            register_age_s=30.0,
            stale=stale,
            degraded=degraded,
            implausible_count=0,
            register_cadence_s=60.0,
            integral_bias_w=None,
            anchor_kind=AnchorKind.REGISTER_LATCHED,
            production_known=False,
        ),
    )


def phase_readings(
    amps: tuple[float, float, float], *, limit_a: float = 63.0, at: datetime = NOW
) -> PhaseReadings:
    """Return one instant's per-phase currents, in amps (D3 §4)."""
    return PhaseReadings(amps=amps, at=at, limit_a=limit_a)


# --------------------------------------------------------------------------- #
# The ceiling and the budget
# --------------------------------------------------------------------------- #


def ceiling(
    kwh: float = 9.70,
    *,
    reason: str = "flat target",
    slack_kwh: float | None = None,
    free_ride: bool = False,
    eligible: bool = True,
    weight: float = 1.0,
) -> Ceiling:
    """Return what D2 says this window may use (D2 §4)."""
    return Ceiling(
        kwh=kwh,
        reason=reason,
        slack_kwh=slack_kwh,
        free_ride=free_ride,
        eligible=eligible,
        weight=weight,
    )


def budget_for(
    *,
    ceiling_kwh: float = 9.70,
    used_kwh: float = 0.0,
    t_rem_h: float = 0.5,
    sigma_w: float | None = 500.0,
    hard_limit_w: float = FUSE_W,
    r_trim_kwh: float = 0.0,
    cfg: BudgetCfg | None = None,
    eligible: bool = True,
    degraded: bool = False,
) -> Budget:
    """Return the budget chain's answer for a window at one instant (D6 §5.1)."""
    return build_budget(
        ceiling(ceiling_kwh, eligible=eligible),
        meter(used_kwh=used_kwh, t_rem_h=t_rem_h, sigma_w=sigma_w, degraded=degraded),
        hard_limit_w,
        PiState(r_trim_kwh=r_trim_kwh),
        cfg or BudgetCfg(),
        None,
    )


def budget_of(p_allow_w: float, **kwargs: Any) -> Budget:
    """Return a budget whose allowance is exactly `p_allow_w` - for walk tests.

    The chain is under test in items 1–4; a test about the *walk* says what the
    allowance is and nothing else, so it is not also a test of the reserve.
    """
    base = budget_for(**kwargs)
    return replace(base, p_allow_w=p_allow_w, p_free_w=p_allow_w)


# --------------------------------------------------------------------------- #
# Loads
# --------------------------------------------------------------------------- #


def comfort(
    *,
    current: float | None = 22.0,
    target: float = 24.0,
    floor: float = 21.0,
    ceiling_c: float | None = 27.0,
    violated: bool = False,
) -> ComfortState:
    """Return a comfort state; `deficit` follows `target − current` (D4 §4.1)."""
    deficit = 0.0 if current is None else target - current
    return ComfortState(
        current=current,
        target=target,
        floor=floor,
        ceiling=ceiling_c,
        violated=violated,
        deficit=deficit,
    )


def demand(**kwargs: Any) -> Demand:
    """Return a `Demand`, defaulting to a load that simply wants its maximum."""
    options: dict[str, Any] = {
        "wants": True,
        "required_kwh": None,
        "deadline": None,
        "min_w": 0.0,
        "max_w": 1000.0,
        "urgency": Urgency.NORMAL,
        "comfort": None,
        "price_sensitive": True,
        "reason": "test",
    }
    options.update(kwargs)
    return Demand(**options)


def ev_quantiser(*, max_a: float = 32.0, phases: int = 1) -> Any:
    """Return the charger's own `quantise`, through the real `Modulate` kind (D4 §5.3).

    The allocator is charged what the device will draw, so the 6 A cliff that dropped
    twelve sessions on the ancestor controller is in the arithmetic, not beside it.
    """
    kind = Modulate(ModulateCfg(unit="a", min_value=6.0, max_value=max_a, step=1.0, cliff=True))

    def quantise(w: float, *, stop_ok: bool, session_active: bool) -> float:
        ctx = KindCtx(
            now=NOW,
            reads=Reads(at=NOW),
            electrical=REFERENCE_PROFILE,
            phases=phases,  # type: ignore[arg-type]
            stop_ok=stop_ok,
            session_active=session_active,
        )
        return kind.quantise(w, ctx).effective_w or 0.0

    return quantise


def ev_view(**kwargs: Any) -> LoadView:
    """Return the reference house's charger: 32 A single phase on L1, priority 10."""
    options: dict[str, Any] = {
        "load_id": "ev",
        "priority": 10,
        "strategy": "deadline_fill",
        "demand": demand(
            min_w=6.0 * W_PER_AMP,
            max_w=32.0 * W_PER_AMP,
            required_kwh=20.0,
            urgency=Urgency.DEADLINE,
            reason="80 % by 07:00",
        ),
        "nameplate_w": 32.0 * W_PER_AMP,
        "kind": "modulate",
        "phase_names": frozenset({"L1"}),
        "phases": 1,
        "quantiser": ev_quantiser(),
    }
    options.update(kwargs)
    return LoadView(**options)


def tank_view(**kwargs: Any) -> LoadView:
    """Return the water heater: a 3 kW element on a relay, priority 20 (D4 §5.6)."""
    options: dict[str, Any] = {
        "load_id": "tank",
        "priority": 20,
        "strategy": "deadline_fill",
        "demand": demand(max_w=3000.0, comfort=comfort(current=62.0, target=65.0, floor=45.0)),
        "nameplate_w": 3000.0,
        "kind": "switch",
        "min_on_s": 600.0,
        "phase_names": frozenset({"L2"}),
        "phases": 1,
    }
    options.update(kwargs)
    return LoadView(**options)


def loop_view(**kwargs: Any) -> LoadView:
    """Return a bathroom floor loop: 960 W under a thermostat, priority 32 (D4 §6.1)."""
    options: dict[str, Any] = {
        "load_id": "loop_bath",
        "priority": 32,
        "strategy": "deadline_fill",
        "demand": demand(max_w=960.0, comfort=comfort()),
        "nameplate_w": 960.0,
        "kind": "setpoint",
        "min_on_s": 900.0,
        "phase_names": frozenset({"L3"}),
        "phases": 1,
    }
    options.update(kwargs)
    return LoadView(**options)


def pump_view(**kwargs: Any) -> LoadView:
    """Return the heat pump: 3 kW rated, priority 50, never shed below stage 4.

    `thermostatic` is what makes its reservation the **measured** figure plus room
    to modulate up: an inverter at 23 W does not reserve 3 kW (D6 §5.2).
    """
    options: dict[str, Any] = {
        "load_id": "pump",
        "priority": 50,
        "strategy": "best_save",
        "demand": demand(max_w=3000.0, comfort=comfort(current=20.5, target=21.0, floor=19.0)),
        "nameplate_w": 3000.0,
        "kind": "setpoint",
        "thermostatic": True,
        "sheddable": False,
        "phases": 3,
    }
    options.update(kwargs)
    return LoadView(**options)


def controlled(
    load_id: str,
    *,
    measured_w: float | None = 0.0,
    commanded_w: float | None = None,
    settling: bool = False,
    phases: frozenset[str] | None = None,
) -> ControlledView:
    """Return one load's meter row for this tick (D3 §4, INV-18)."""
    return ControlledView(
        load_id=load_id,
        measured_w=measured_w,
        commanded_w=commanded_w,
        settling=settling,
        phases=phases,
    )


# --------------------------------------------------------------------------- #
# Prices - what a zone costs its sources with (D1 §4)
# --------------------------------------------------------------------------- #


def curve_of(
    value: str,
    *,
    carrier: Carrier = Carrier.ELECTRICITY,
    now: datetime = NOW,
    hours: int = 6,
) -> PriceCurve:
    """Return a flat import curve at `value` major units per kWh (D1 §4).

    Flat because a zone asks one question - what does a kilowatt-hour of this
    carrier cost **now** - and a curve that varies would test D1, not D6.
    """
    total = Decimal(value)
    start = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    slots = tuple(
        Slot(
            start=start + timedelta(hours=hour),
            end=start + timedelta(hours=hour + 1),
            total=total,
            components={"spot": total},
            confidence=Confidence.KNOWN,
        )
        for hour in range(hours)
    )
    return PriceCurve(
        carrier=carrier,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=slots,
        built_at=now,
        sources=("test",),
    )


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


def plan_of(
    load_id: str,
    *caps: tuple[int, float | None],
    now: datetime = NOW,
    minutes: int = 15,
    mode: PlanMode = PlanMode.PRICE,
) -> Plan:
    """Return a plan whose slots are `(offset_min, envelope_w)` pairs from `now`."""
    slots = tuple(
        PlanSlot(
            start=now + timedelta(minutes=offset),
            end=now + timedelta(minutes=offset + minutes),
            envelope_w=envelope,
            reason="test",
        )
        for offset, envelope in caps
    )
    return Plan(
        load_id=load_id,
        strategy="deadline_fill",
        mode=mode,
        slots=slots,
        built_at=now,
        cost_estimate=Money(Decimal(0), "NOK"),
        confidence=Confidence.KNOWN,
    )


def free_plan(load_id: str) -> Plan:
    """Return the plan of a load nothing paces: `cap_w` answers `None` (INV-30)."""
    return plan_of(load_id, mode=PlanMode.NONE)


# --------------------------------------------------------------------------- #
# The tick
# --------------------------------------------------------------------------- #


def alloc_ctx(
    loads: Sequence[LoadView],
    *,
    budget: Budget | None = None,
    plans: Mapping[str, Plan] | None = None,
    views: Mapping[str, ControlledView] | None = None,
    previous: Mapping[str, Grant] | None = None,
    meter_snapshot: MeterSnapshot | None = None,
    stage: int = 0,
    blunt: bool = False,
    frozen: bool = False,
    hard: HardLimits | None = None,
    now: datetime = NOW,
) -> AllocCtx:
    """Return one tick's inputs (D6 §4).

    A load's `Demand` rides on its own `LoadView`; `plans` defaults to no plan at
    all and `views` to "nothing measured yet" - the state a fresh site is in.
    """
    return AllocCtx(
        now=now,
        meter=meter_snapshot if meter_snapshot is not None else meter(),
        budget=budget if budget is not None else budget_of(10_000.0),
        electrical=REFERENCE_PROFILE,
        loads=tuple(loads),
        plans={} if plans is None else plans,
        views={load.load_id: controlled(load.load_id) for load in loads}
        if views is None
        else views,
        previous={} if previous is None else previous,
        stage=stage,
        blunt=blunt,
        frozen=frozen,
        hard=hard if hard is not None else HardLimits(fuse_w=FUSE_W),
    )


def granted(grant: Grant | None) -> float:
    """Return a grant's watts, 0 for a load that was not in the grants at all."""
    return 0.0 if grant is None else grant.w


def state_with(**kwargs: Any) -> AllocState:
    """Return an `AllocState`, empty but for what the test names."""
    return AllocState(**kwargs)


def constraints(*items: Constraint) -> tuple[Constraint, ...]:
    """Return the constraint list in D6 §2's order - site, circuit, phase."""
    return items


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #


def ladder_budget(
    *,
    projected_kwh: float,
    ceiling_kwh: float = 9.70,
    used_kwh: float = 0.0,
    reserve_kwh: float = 0.375,
    p_allow_w: float = 6650.0,
) -> Budget:
    """Return the budget the ladder reads: the projection and the four numbers.

    The projection is a **smoothed** figure (INV-38); a test that wants to show
    what the instantaneous reading would have said computes both.
    """
    base = budget_for(ceiling_kwh=ceiling_kwh, used_kwh=used_kwh)
    return replace(
        base,
        reserve_kwh=reserve_kwh,
        p_allow_w=p_allow_w,
        p_free_w=p_allow_w,
        projected_kwh=projected_kwh,
    )
