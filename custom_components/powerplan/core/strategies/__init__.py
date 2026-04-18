"""D5 - strategies and planning: when a load should run, and inside what (HLD §6.5).

The price axis. For each load this package turns a composed price curve and a
`Demand` into a `Plan`: *when* to run and with what envelope, in one uniform shape
so the allocator only ever asks one question - `plan.cap_w(now)`, which answers
`None` (no plan, control freely), `0` (stand still) or a cap in watts. The three
are different answers and the distinction survives every layer (INV-30).

A plan **paces**; it never overrides safety. The precedence lives in
`core/allocation/` and nowhere else (INV-1), so nothing here may raise a grant,
open a gate or stop a load: the most a strategy can do is say what would be cheap.

Importing this package registers every strategy that ships. WP0.6 ships two -
`deadline_fill` and `always` - and `cheapest_hours`, `best_save`,
`heat_capacitor`, `run_once`, `schedule`, the combinators and the battery pair
land as modules that call `@register` and change nothing else (D5 §3).
"""

from ..model import DesiredState, Plan, PlanMode, PlanSlot, SetpointDelta
from .adoption import (
    COMMIT_MIN,
    MIN_REPLAN_INTERVAL_S,
    REQUIREMENT_TOLERANCE,
    ReplanTrigger,
    committed_slots,
    inputs_changed,
    replan_due,
    should_adopt,
)
from .always import Always
from .base import (
    COMMON_SCHEMA,
    Strategy,
    StrategyEntry,
    entry,
    free_plan,
    get,
    keys,
    params_of,
    plan_all,
    register,
    supports,
)
from .context import (
    CeilingSource,
    Curves,
    Forecasts,
    Headroom,
    LoadView,
    PlanContext,
    SiteContext,
    SitePlan,
)
from .deadline_fill import DeadlineFill, FlatPolicy, plan_one
from .deadlines import DeadlineNeed, first, needs
from .plan import COVER_EPS_KWH, build_plan, inputs_digest

__all__ = [
    "COMMIT_MIN",
    "COMMON_SCHEMA",
    "COVER_EPS_KWH",
    "MIN_REPLAN_INTERVAL_S",
    "REQUIREMENT_TOLERANCE",
    "Always",
    "CeilingSource",
    "Curves",
    "DeadlineFill",
    "DeadlineNeed",
    "DesiredState",
    "FlatPolicy",
    "Forecasts",
    "Headroom",
    "LoadView",
    "Plan",
    "PlanContext",
    "PlanMode",
    "PlanSlot",
    "ReplanTrigger",
    "SetpointDelta",
    "SiteContext",
    "SitePlan",
    "Strategy",
    "StrategyEntry",
    "build_plan",
    "committed_slots",
    "entry",
    "first",
    "free_plan",
    "get",
    "inputs_changed",
    "inputs_digest",
    "keys",
    "needs",
    "params_of",
    "plan_all",
    "plan_one",
    "register",
    "replan_due",
    "should_adopt",
    "supports",
]
