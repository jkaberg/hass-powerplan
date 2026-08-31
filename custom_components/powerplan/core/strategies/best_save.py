"""`best_save` - powersaver's postponement strategy, restated (D5 §5.5).

The insight worth keeping from powersaver is that this strategy only ever says
**wait**. It turns a load off in a slot whose price is far enough above the slot
the load would run in instead, and it says nothing at all about the slots it
leaves on: `envelope_w = None` there, so the allocator controls the load against
the ceiling and the comfort floors exactly as it would with `always` (INV-30). It
never forces consumption - that is `heat_capacitor`'s and `opportunistic`'s job,
and D5 §11 rejected making it symmetric on purpose.

Three rules bound the postponement, and each of them comes from a defect:

* `max_off_min` - a slab told to coast on price alone coasts all afternoon;
* `min_on_min` - the tank that flipped 75 → 45 → 75 → 45 in 23 minutes, none of
  which stored any useful energy (D4 §5.4);
* `recovery_min = recovery_factor × off_minutes` - a store that has been coasting
  needs the next stretch to put back what it lost, whatever the price is doing.

**What "the next on slot" means here.** §5.5's pseudocode defines the comparison
slot as "the next slot after `s` that is on", while on/off is exactly what the
pass is deciding - the definition is circular. The comparison slot is therefore
the cheapest slot inside the postponement horizon `(s, s + max_off_min]`: it is
the slot the load would actually run in if this one were skipped, which is what
the saving is a saving against (`design/DECISIONS.md` D-0192).

**A negative slot is never postponed.** The threshold is a fraction *of the slot
price*, so at −0.05 kr it would be negative and any later slot at all would look
like a saving. Consuming at a negative price is paid for (INV-51), so the
threshold is floored at zero and the saving must be strictly positive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal

from ..model import Desired, PlanMode, PlanSlot, Slot
from ..pricing import Field, FieldKind, Schema
from .base import free_plan, register
from .holding import comfort_target, holding_kwh
from .plan import build_plan, confidence_of, inputs_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..model import Demand, Plan
    from .context import PlanContext

__all__ = ["BestSave"]

#: A saving has to be a saving: below this many minor units it is float noise.
_EPS: Final = Decimal("0.0000001")


@dataclass(frozen=True, slots=True)
class _Policy:
    """The four knobs of §5.5, resolved from the schema's defaults (D5 §6)."""

    min_saving: float
    max_off_min: float
    min_on_min: float
    recovery_factor: float
    absolute: Decimal | None

    def threshold(self, price: Decimal) -> Decimal:
        """Return what the saving must clear in this slot (§5.5, INV-51).

        Floored at zero: a fraction of a negative price is a negative bar, and
        "turn off because the price is below zero" is backwards.
        """
        if self.absolute is not None:
            return self.absolute
        return max(Decimal(0), Decimal(str(self.min_saving)) * price)


def _policy(params: Mapping[str, Any]) -> _Policy:
    """Return the policy the parameters describe."""
    absolute = params["min_saving_absolute"]
    return _Policy(
        min_saving=float(params["min_saving"]),
        max_off_min=float(params["max_off_min"]),
        min_on_min=float(params["min_on_min"]),
        recovery_factor=float(params["recovery_factor"]),
        absolute=None if absolute is None else Decimal(str(absolute)),
    )


def _alternative(slots: Sequence[Slot], index: int, horizon_min: float) -> Decimal | None:
    """Return the cheapest price within the postponement horizon after `index`.

    The slot the load would run in instead. `None` when the horizon holds no
    slot - at the very end of the curve there is nothing to postpone *to*, and a
    load is never turned off on the strength of a slot that does not exist.
    """
    best: Decimal | None = None
    minutes = 0.0
    for slot in slots[index + 1 :]:
        best = slot.total if best is None else min(best, slot.total)
        minutes += (slot.end - slot.start).total_seconds() / 60.0
        if minutes >= horizon_min:
            break
    return best


def _decide(slots: Sequence[Slot], policy: _Policy) -> list[bool]:
    """Return, per slot, whether the load is postponed there (§5.5).

    One forward pass in time order. `on_run` starts at `min_on_min` because the
    load was running before the horizon began: starting it at zero would make the
    first half hour of every plan unconditionally on, for no physical reason.
    """
    off: list[bool] = []
    off_run = 0.0
    on_run = policy.min_on_min
    recovery_left = 0.0

    for index, slot in enumerate(slots):
        span = (slot.end - slot.start).total_seconds() / 60.0
        if recovery_left > 0.0:
            recovery_left -= span
            on_run += span
            off_run = 0.0
            off.append(False)
            continue

        alternative = _alternative(slots, index, policy.max_off_min)
        saving = Decimal(0) if alternative is None else slot.total - alternative
        postpone = (
            saving > _EPS
            and saving >= policy.threshold(slot.total)
            and off_run + span <= policy.max_off_min
            and on_run >= policy.min_on_min
        )
        if postpone:
            off_run += span
            on_run = 0.0
        else:
            if off_run > 0.0:
                recovery_left = max(0.0, policy.recovery_factor * off_run - span)
            off_run = 0.0
            on_run += span
        off.append(postpone)
    return off


def _slot(slot: Slot, *, postponed: bool, kind: str, hold_kwh: float = 0.0) -> PlanSlot:
    """Return one slot of the plan: standing still, or free (INV-30) with what holding costs (D-0501)."""
    return PlanSlot(
        start=slot.start,
        end=slot.end,
        envelope_w=0.0 if postponed else None,
        desired_state=Desired.SHED if postponed and kind in {"mode", "setpoint"} else None,
        kwh=0.0,
        hold_kwh=0.0 if postponed else hold_kwh,
        price=slot.total,
        reason="postponed" if postponed else "",
    )


@register
class BestSave:
    """Off where waiting pays, free everywhere else (D5 §5.5, §6)."""

    key: ClassVar[str] = "best_save"
    supports: ClassVar[frozenset[str] | Literal["all"]] = frozenset(
        {"floor_heating", "heat_pump", "radiator", "water_heater", "generic_switch"}
    )
    schema: ClassVar[Schema] = (
        Field(key="min_saving", kind=FieldKind.NUMBER, default=0.10),
        Field(key="max_off_min", kind=FieldKind.NUMBER, default=120, unit="min"),
        Field(key="min_on_min", kind=FieldKind.NUMBER, default=30, unit="min"),
        Field(key="recovery_factor", kind=FieldKind.NUMBER, default=0.5, advanced=True),
        Field(key="min_saving_absolute", kind=FieldKind.MONEY, default=None, advanced=True),
    )

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return the postponement plan over the horizon (§5.5).

        A demand with no vote - a comfort violation, a legionella cycle, a min-SoC
        floor - is not postponed at all: the plan says nothing and the allocator
        serves it (D5 §8, `design/DECISIONS.md` D-0138).
        """
        if ctx.load.forced:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.FORCE, reason="forced")
        if not demand.price_sensitive:
            return free_plan(ctx, strategy=self.key, mode=PlanMode.URGENT, reason=demand.reason)

        policy = _policy(params)
        window = ctx.curve_in.slots_between(ctx.now, ctx.horizon_end())
        off = _decide(window, policy)
        postponed = [slot for slot, skip in zip(window, off, strict=True) if skip]
        return build_plan(
            load_id=ctx.load.load_id,
            strategy=self.key,
            mode=PlanMode.PRICE,
            slots=tuple(
                _slot(slot, postponed=skip, kind=ctx.load.kind, hold_kwh=_hold(ctx, demand, slot))
                for slot, skip in zip(window, off, strict=True)
            ),
            now=ctx.now,
            currency=ctx.curve_in.currency,
            confidence=confidence_of(postponed),
            known_until=ctx.now + timedelta(hours=ctx.curve_in.coverage_h(ctx.now)),
            reason=_reason(postponed, policy),
            inputs_hash=inputs_digest(
                self.key, ctx.load.mode, demand.price_sensitive, sorted(params.items(), key=str)
            ),
        )


def _hold(ctx: PlanContext, demand: Demand, slot: Slot) -> float:
    """Return what the store draws holding its target in a free slot (D-0501); 0 with no target."""
    target = comfort_target(ctx, demand, slot.start)
    return 0.0 if target is None else holding_kwh(ctx, slot.start, slot.end, target)


def _reason(postponed: Sequence[Slot], policy: _Policy) -> str:
    """Return the one line the review sensor shows (D5 §8)."""
    if not postponed:
        return "nothing worth postponing"
    minutes = sum((slot.end - slot.start).total_seconds() / 60.0 for slot in postponed)
    bar = policy.absolute if policy.absolute is not None else f"{policy.min_saving:.0%}"
    return f"postponing {minutes:.0f} min where waiting saves {bar}"
