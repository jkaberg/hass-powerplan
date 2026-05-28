"""The constraints, in evaluation order (D6 §2, §3).

Site hard limits → circuits → phases → groups → zones: outermost physical limit
first, preferences last, each applied as a `min()` so an inner limit can only
tighten what an outer one allowed (INV-60). A `CycleReservation` is scoped to one
load and pins rather than caps: it is what a started programme holds (INV-59).
"""

from .base import AllocCtx, Constraint, MarginalCost, Scope, Violation
from .circuit import CircuitLimit, CircuitSpec
from .cycle import CycleReservation
from .group import GroupCap, default_max_concurrent_w
from .hard import ContractedPowerLimit, ExternalLimit, SiteFuse
from .phase import PhaseLimit
from .zone import Zone, ZoneChoice, ZoneSource

__all__ = [
    "AllocCtx",
    "CircuitLimit",
    "CircuitSpec",
    "Constraint",
    "ContractedPowerLimit",
    "CycleReservation",
    "ExternalLimit",
    "GroupCap",
    "MarginalCost",
    "PhaseLimit",
    "Scope",
    "SiteFuse",
    "Violation",
    "Zone",
    "ZoneChoice",
    "ZoneSource",
    "default_max_concurrent_w",
]
