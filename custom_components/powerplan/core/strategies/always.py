"""`always` - no price steering at all (HLD §6.5, D5 §6).

A load with `strategy = always` is pure capacity management: the plan has nothing
to say, so `cap_w` answers `None` in every slot and the allocator controls the
load freely against the hard limits, the ceiling and the comfort floors (INV-1,
INV-30). It is the powersaver-free baseline, the honest answer for a load whose
price elasticity is zero, and - with `NoPeak` - the configuration in which
powerplan does nothing at all.

`None` is not `0`: a zero envelope means "stand still this slot", which is a
decision. Saying nothing is not one (INV-30).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Literal

from ..model import PlanMode
from .base import free_plan, register

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..model import Demand, Plan
    from ..pricing import Schema
    from .context import PlanContext

__all__ = ["Always"]


@register
class Always:
    """Never steer this load by price (D5 §6)."""

    key: ClassVar[str] = "always"
    supports: ClassVar[frozenset[str] | Literal["all"]] = "all"
    schema: ClassVar[Schema] = ()

    def plan(self, demand: Demand, ctx: PlanContext, params: Mapping[str, Any]) -> Plan:
        """Return a plan that defers to the allocator in every slot."""
        return free_plan(ctx, strategy=self.key, mode=PlanMode.NONE, reason="no price steering")
