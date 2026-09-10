"""The `Constraint` protocol, the `Violation` and one tick's inputs (D6 §2, §4).

Evaluation order is **site hard limits → circuits → phases → groups → zones**:
outermost physical limit first, preferences last. Each constraint's `cap_w` is
applied as `min(...)` while the walk goes down the priority order, so an inner
limit can only ever tighten what an outer one allowed (INV-60). A constraint may
only ever take away - it decides nothing about *who* gets power, which is the
allocator's, and the precedence lives there and nowhere else (INV-1).

`group.py`, `zone.py` and `cycle.py` implement this same
protocol and the walk takes them unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol

from ...metering import ControlledView, ElectricalProfile, MeterSnapshot
from ...model import Grant, Plan
from ...strategies import LoadView
from ..budget import Budget
from ..ladder import HardLimits
from ..reserved import reserved_w

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...model import Money
    from ..report import ShedReason

__all__ = ["AllocCtx", "Constraint", "MarginalCost", "Scope", "Violation"]

#: Where a constraint applies, in evaluation order (D6 §2).
type Scope = Literal["site", "circuit", "phase", "group", "zone", "load", "switched"]


class MarginalCost(Protocol):
    """D2's "what does going over cost" - the v2 cost-based trade-off hook (D6 §10).

    Carried on `AllocCtx` and deliberately unused in v1: today the stage table
    encodes the trade-off (D6 §10).
    """

    def __call__(self, kw_over: float, now: datetime) -> Money:
        """Return what `kw_over` above the metric-neutral point costs this period."""
        ...


@dataclass(frozen=True, slots=True)
class Violation:
    """What a constraint finds still broken after the grants are decided (D6 §4).

    `blunt` is what makes it a `fuse_breach` rather than a number: a circuit or a
    phase fuse is a physical fact, and it is scoped to `members` - a 32 A garage
    fuse says nothing about the other 60 amps of the house (INV-60).
    """

    constraint: str
    scope_id: str
    excess_w: float
    members: tuple[str, ...]
    blunt: bool


@dataclass(frozen=True, slots=True)
class AllocCtx:
    """Everything one allocation tick depends on outside the constraints (D6 §4).

    One frozen bundle, which is also what the constraints read in `prepare()`:
    `tick(state, inputs)` (HLD §5). `plans` is D5's output per load - absent means
    "no plan, control freely" (INV-30) - and `previous` is last tick's grants, which
    is what a load gives back before it asks (D6 §5.3 step 2).
    """

    now: datetime
    meter: MeterSnapshot
    budget: Budget
    electrical: ElectricalProfile
    loads: tuple[LoadView, ...] = ()
    plans: Mapping[str, Plan] = field(default_factory=dict)
    views: Mapping[str, ControlledView] = field(default_factory=dict)
    previous: Mapping[str, Grant] = field(default_factory=dict)
    stage: int = 0
    blunt: bool = False
    frozen: bool = False
    hard: HardLimits | None = None
    marginal_cost: MarginalCost | None = None
    #: This tick's sub-meter power per circuit key (D3 §2, D6 §5.8): a key is
    #: present for every sub-metered circuit, and `None` when its meter cannot
    #: answer, which is the fall-back to Σ members + `unmetered_w` (D6 §8). A
    #: circuit without a key has no sub-meter.
    circuits: Mapping[str, float | None] = field(default_factory=dict)

    def load(self, load_id: str) -> LoadView | None:
        """Return one load's view, or `None` when it is not in this tick."""
        for view in self.loads:
            if view.load_id == load_id:
                return view
        return None

    def view(self, load_id: str) -> ControlledView | None:
        """Return one load's meter row, or `None` when nothing measures it."""
        return self.views.get(load_id)

    def previous_w(self, load_id: str) -> float:
        """Return what this load was granted last tick, 0 when it is new."""
        grant = self.previous.get(load_id)
        return 0.0 if grant is None else grant.w

    def held_w(self, load_id: str) -> float:
        """Return what this load is holding now: its previous grant's reservation."""
        view = self.load(load_id)
        if view is None:
            return 0.0
        return reserved_w(view, self.previous_w(load_id), self.views.get(load_id))

    def reserved_of(self, granted: Mapping[str, float], *, without: str = "") -> float:
        """Return what the loads in `granted` reserve, `without` one of them.

        The units a constraint has to subtract: an on/off element's nameplate, a
        thermostatic load's measured draw plus its margin, a charger's grant
        (D6 §5.2) - never the paced grant of a relay.
        """
        total = 0.0
        for load_id, watts in granted.items():
            if load_id == without:
                continue
            view = self.load(load_id)
            if view is not None:
                total += reserved_w(view, watts, self.views.get(load_id))
        return total


class Constraint(Protocol):
    """One limit the allocator must respect (D6 §2).

    `key` names it in `Grant.capped_by` and in the report - the class's own for a
    site-wide limit, the instance's for a circuit, a group or a zone, which is why
    it is not a `ClassVar`; `shed_reason` is what a load denied by it is shed
    *for*, so a new constraint is one module with one reason and no conditional
    anywhere else ("extension is by registry").
    """

    scope: ClassVar[Scope]
    shed_reason: ClassVar[ShedReason]

    @property
    def key(self) -> str:
        """Name this constraint in `Grant.capped_by` and the report."""
        ...

    def prepare(self, ctx: AllocCtx) -> None:
        """Read the measurements and compute this tick's caps."""
        ...

    def cap_w(self, load: LoadView, granted_so_far: Mapping[str, float]) -> float | None:
        """Return the most `load` may take now; `None` is "no opinion"."""
        ...

    def post(self, grants: Mapping[str, float]) -> Sequence[Violation]:
        """Return what is still violated once the grants are decided."""
        ...

    def reserve_w(self) -> float:
        """Return the power this constraint pins: delegated loads, running cycles."""
        ...
