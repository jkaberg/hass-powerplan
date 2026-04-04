"""The `PriceModifier` protocol and the helper every modifier uses (D1 §4, §5.3).

A modifier is a pure function of `(slot, ctx)` that adds or replaces **exactly
one** component (INV-4). The breakdown is what a dashboard stacks, what the
review step explains, what the Norgespris cap needs and what answers "why is my
price wrong"; a scalar total would lose all four. Component names are fixed
strings for the same reason.

`with_component` keeps `total` equal to Σ components, so nothing downstream
recomputes it - and nothing clamps a component or a total at zero (INV-51).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Protocol

if TYPE_CHECKING:
    from ..context import PriceContext
    from ..model import Schema, Slot

#: The energy component: spot, or whatever replaced it (`fixed_price`).
SPOT = "spot"
#: The grid's energy charge - `energiledd`, day/night or time-of-use.
GRID_ENERGY = "grid_energy"


class PriceModifier(Protocol):
    """How a raw price becomes what you actually pay (D1 §4).

    Extension is by registry: a new modifier is one module that defines a
    frozen dataclass with these class attributes and calls
    `registry.register` on it. Nothing anywhere switches on `key`.
    """

    key: ClassVar[str]
    component: ClassVar[str]
    schema: ClassVar[Schema]

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with this modifier's component written (pure)."""
        ...


def with_component(slot: Slot, name: str, value: Decimal) -> Slot:
    """Return `slot` with component `name` set and `total` = Σ components (INV-4).

    Replacing an existing component keeps its position in the breakdown, so the
    order a dashboard stacks does not change when a modifier re-runs.
    """
    components = dict(slot.components)
    components[name] = value
    return replace(slot, components=components, total=sum(components.values(), Decimal(0)))
