"""`surplus` - run on the sun, and on the grid only for what the sun cannot cover (D5 §2).

evcc's "PV" mode (Phase 7). Every ranking strategy already plans on the
effective curve, so a tank on `deadline_fill` beside panels heats at noon when
the surplus is cheaper than the night (D5 §2, §11). `surplus` is the stricter
mode a household picks knowingly: the load plans in the surplus bands alone,
and takes grid energy only for the part of its requirement the forecast surplus
cannot deliver before the deadline (`grid_top_up`, default on).

The arithmetic is `deadline_fill`'s greedy over `(slot, band)` pairs with every
grid band ranked `GRID_RANK` dearer than its price, never priced so: every
surplus kWh before the deadline is taken before the first grid kWh, and the
grid kWh that are needed are still the cheapest ones (§5.2's exactness holds -
the penalty is one constant on one side of the order). A load with no
requirement (a switch that just runs) or no deadline has nothing to top up and
runs on the sun alone.

In the tick D6 follows the measured surplus (D6 §5.3): the plan's `surplus_w`
share of a slot is what a forecast said, and `grid_w` is all it may import.
`min_surplus_w` is the surplus a load needs before it starts, 0 for its own
floor (D6's enable delay, 60 s).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..model import PlanMode
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .deadline_fill import plan_one
from .plan import inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..model import Demand, Plan
    from .context import PlanContext

__all__ = ["GRID_RANK", "Surplus"]

#: How much dearer a grid kWh ranks than a surplus one: more than any spread a
#: curve can have, so the order is "all the sun first", not a price comparison.
GRID_RANK: Final = Decimal(1_000_000)


@register
class Surplus:
    """Plan on the forecast surplus alone, the grid only for a deadline's shortfall (D5 §2)."""

    key: ClassVar[str] = "surplus"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"ev", "water_heater", "generic_switch"}
    )
    schema: ClassVar[Schema] = (
        Field(key="grid_top_up", kind=FieldKind.BOOL, default=True),
        Field(
            key="min_surplus_w",
            kind=FieldKind.NUMBER,
            default=0,
            unit="W",
            advanced=True,
        ),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the load's surplus plan, topped up from the grid before its deadline."""
        if not demand.price_sensitive and not ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.URGENT, reason=demand.reason)
        required = demand.required_kwh
        top_up = (
            bool(params["grid_top_up"]) and required is not None and demand.deadline is not None
        ) or ctx.load.forced
        reason = "" if ctx.surplus else "no surplus forecast"
        return plan_one(
            ctx.curve_in,
            required_kwh=_all_the_sun(ctx, demand.max_w) if required is None else required,
            max_w=demand.max_w,
            headroom=ctx.headroom,
            now=ctx.now,
            horizon_end=ctx.horizon_end(),
            deadline=demand.deadline,
            min_w=demand.min_w,
            force=ctx.load.forced,
            load_id=ctx.load.load_id,
            strategy=self.key,
            reason=reason,
            surplus=ctx.surplus_bands,
            grid=top_up,
            grid_penalty=GRID_RANK,
            inputs_hash=inputs_digest(
                self.key,
                ctx.load.mode,
                demand.deadline,
                demand.max_w,
                demand.min_w,
                sorted(params.items()),
                ctx.presence,
            ),
        )


def _all_the_sun(ctx: PlanContext, max_w: float) -> float:
    """Return the kWh a load without a requirement can take from the surplus, horizon-wide."""
    total = 0.0
    for slot in ctx.curve_in.slots_between(ctx.now, ctx.horizon_end()):
        watts = min(max_w, ctx.surplus.get(slot.start, 0.0))
        total += watts * (slot.end - slot.start).total_seconds() / 3600.0 / 1000.0
    return total
