"""The constraints, in evaluation order (D6 §2, §3).

Site hard limits → circuits → phases → groups → zones: outermost physical limit
first, preferences last, each applied as a `min()` so an inner limit can only
tighten what an outer one allowed (INV-60). `group.py`, `zone.py`
and `cycle.py` implement the same protocol and the walk takes them
unchanged.
"""

from .base import AllocCtx, Constraint, MarginalCost, Scope, Violation
from .circuit import CircuitLimit
from .hard import ContractedPowerLimit, ExternalLimit, SiteFuse
from .phase import PhaseLimit

__all__ = [
    "AllocCtx",
    "CircuitLimit",
    "Constraint",
    "ContractedPowerLimit",
    "ExternalLimit",
    "MarginalCost",
    "PhaseLimit",
    "Scope",
    "SiteFuse",
    "Violation",
]
