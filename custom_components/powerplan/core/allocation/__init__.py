"""D6 - allocation, constraints and shedding: the precedence, executable (HLD §6.6).

Both axes meet here. The price axis arrives as a `Plan` per load, the capacity axis
as a `Ceiling` and the hard limits, and this package answers one question per tick:
**what may each load draw right now, and why**.

    budget(ceiling, meter, hard_limit_w, pi, cfg, baseline) → Budget
    allocate(ctx, constraints, cfg, state)                  → (Grants, AllocReport, AllocState)
    Ladder().update(budget, …)                              → LadderState
    proportional_trim(loads, grants, deficit_w, …)          → (Grants, freed_w)

**INV-1 lives here and nowhere else**: hard limits > capacity ceiling > comfort
floors > plan > preference. A strategy paces and never overrides safety; a device
profile never decides; and the one named exception to the ceiling - a violated
comfort floor is served up to the hard limits - is in `allocator.py` step 3 and
nowhere else (HLD §3).

Nothing here imports `core.accounting` (INV-68): D6 decides, D11 observes, and the
tick never touches the ledger.
"""

from .allocator import EV_MIN_STOP_S, AllocCfg, AllocState, Grants, allocate
from .budget import (
    DEGRADED_BUMP_KWH,
    SIGMA_FLOOR_W,
    Baseline,
    Budget,
    BudgetCfg,
    PiState,
    allowance_w,
    budget,
    frozen_for,
    is_outlier,
    pi_close,
    pi_update,
    projection_kwh,
    reserve_kwh,
)
from .constraints import (
    AllocCtx,
    CircuitLimit,
    Constraint,
    ContractedPowerLimit,
    CycleReservation,
    ExternalLimit,
    GroupCap,
    MarginalCost,
    PhaseLimit,
    Scope,
    SiteFuse,
    Violation,
    Zone,
    ZoneChoice,
    ZoneSource,
    default_max_concurrent_w,
)
from .ladder import (
    BLUNT_REASONS,
    STAGE_TRIM,
    HardLimits,
    Ladder,
    LadderCfg,
    LadderState,
    cap_for_projection,
    reason_key,
    stage_for,
)
from .report import (
    AllocReport,
    BreachLog,
    CircuitReport,
    PhaseReport,
    ReservedRow,
    RotationReport,
    ShedReason,
    ZoneReport,
    reservation_table,
    unconstrained_ask_w,
)
from .reserved import GRANT_MARGIN_W, MODULATING_KINDS, ON_W, measured_w, reserved_w
from .trim import TrimCfg, proportional_trim, trim_candidates

__all__ = [
    "BLUNT_REASONS",
    "DEGRADED_BUMP_KWH",
    "EV_MIN_STOP_S",
    "GRANT_MARGIN_W",
    "MODULATING_KINDS",
    "ON_W",
    "SIGMA_FLOOR_W",
    "STAGE_TRIM",
    "AllocCfg",
    "AllocCtx",
    "AllocReport",
    "AllocState",
    "Baseline",
    "BreachLog",
    "Budget",
    "BudgetCfg",
    "CircuitLimit",
    "CircuitReport",
    "Constraint",
    "ContractedPowerLimit",
    "CycleReservation",
    "ExternalLimit",
    "Grants",
    "GroupCap",
    "HardLimits",
    "Ladder",
    "LadderCfg",
    "LadderState",
    "MarginalCost",
    "PhaseLimit",
    "PhaseReport",
    "PiState",
    "ReservedRow",
    "RotationReport",
    "Scope",
    "ShedReason",
    "SiteFuse",
    "TrimCfg",
    "Violation",
    "Zone",
    "ZoneChoice",
    "ZoneReport",
    "ZoneSource",
    "allocate",
    "allowance_w",
    "budget",
    "cap_for_projection",
    "default_max_concurrent_w",
    "frozen_for",
    "is_outlier",
    "measured_w",
    "pi_close",
    "pi_update",
    "projection_kwh",
    "proportional_trim",
    "reason_key",
    "reservation_table",
    "reserve_kwh",
    "reserved_w",
    "stage_for",
    "trim_candidates",
    "unconstrained_ask_w",
]
