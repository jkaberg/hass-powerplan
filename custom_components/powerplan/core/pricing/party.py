"""The modifier chain by party, built from the site's tariff copy (D1 §5.3, D13 §8).

Order: the **supplier**'s components (the household's add-ons, a price-replacing
one first) → the **grid company**'s energy charge from the copy → the **state**:
the zone's levies, the schemes the household is in, VAT last. Each stage adds
only what the price source does not already include (INV-72): a source declares
its basis ⊆ {spot, grid, vat, levies}.

A copy stores its prices as published (INV-71), so a grid charge published incl.
VAT and levies carries the state's share of the national rates on its date. The
grid stage takes that share out and the state stage puts the household's own
back - once, at the slot's date and zone (D13 §18 G16). The breakdown therefore
names every øre by its party whatever the copy's basis, and a copy stored excl.
VAT composes to the same slots as one stored incl. (D13 §19 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final

from ..model import Confidence
from ..tariffs.household import (
    HouseholdPrice,
    Party,
    levies_at,
    published_levies_at,
    published_vat_at,
    vat_at,
)
from .model import Schema, Slot
from .modifiers.base import GRID_ENERGY, SPOT, PriceModifier, with_component

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime

    from .context import PriceContext

__all__ = [
    "BASIS_KEYS",
    "PARTY",
    "GridEnergy",
    "GridShare",
    "StateLevies",
    "StateVat",
    "chain",
    "party_of",
]

LEVY: Final = "levy"
VAT: Final = "vat"
SUBSIDY: Final = "subsidy"

#: What a price source may already include (D1 §5.3, D13 §8, O5).
BASIS_KEYS: Final = frozenset({"spot", "grid", "vat", "levies"})

#: Every D1 component and its party (D1 §5.3's table, INV-72).
PARTY: Final[Mapping[str, Party]] = {
    SPOT: Party.SUPPLIER,
    "supplier": Party.SUPPLIER,
    "tier": Party.SUPPLIER,
    "day_type": Party.SUPPLIER,
    GRID_ENERGY: Party.GRID,
    LEVY: Party.STATE,
    VAT: Party.STATE,
    SUBSIDY: Party.STATE,
}


def party_of(component: str) -> Party:
    """Return the party a component belongs to."""
    return PARTY[component]


def _day(when: datetime, ctx: PriceContext) -> date:
    return when.astimezone(ctx.tz).date()


@dataclass(frozen=True, slots=True)
class GridEnergy:
    """The grid's energy charge from the copy, net of the state's share (D13 §8).

    Built from the copy, never configured, so it is not in the modifier registry
    and no flow offers it.
    """

    key: ClassVar[str] = "grid_energy"
    component: ClassVar[str] = GRID_ENERGY
    schema: ClassVar[Schema] = ()

    price: HouseholdPrice

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the grid's own charge at `when`, excl. VAT and levies."""
        grid = self.price.grid
        day = _day(when, ctx)
        version = grid.energy_at(day)
        if version is None:
            return Decimal(0)
        published = next(
            (
                period.price
                for period in version.periods
                if period.when is None or period.when.matches(when, ctx.tz, ctx.holidays)
            ),
            version.fallback,
        )
        state = self.price.state
        if grid.basis.vat:
            published /= Decimal(1) + published_vat_at(state, day)
        return published - published_levies_at(state, day, grid.basis)

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the grid energy component written (INV-4)."""
        return with_component(slot, self.component, self.price_at(slot.start, ctx))


@dataclass(frozen=True, slots=True)
class StateLevies:
    """The zone's levies per kWh at the slot's date, one `levy` component (D1 §5.4)."""

    key: ClassVar[str] = "state_levies"
    component: ClassVar[str] = LEVY
    schema: ClassVar[Schema] = ()

    price: HouseholdPrice

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the levy component written (INV-4)."""
        amounts = levies_at(self.price.state, _day(slot.start, ctx))
        return with_component(slot, self.component, sum(amounts.values(), Decimal(0)))


@dataclass(frozen=True, slots=True)
class StateVat:
    """The zone's VAT at the slot's date on the components that exclude it (D1 §5.4).

    `taxed` is decided once, from the bases: a component the price source or the
    supplier already quotes with VAT is not in it.
    """

    key: ClassVar[str] = "state_vat"
    component: ClassVar[str] = VAT
    schema: ClassVar[Schema] = ()

    price: HouseholdPrice
    taxed: frozenset[str]

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the VAT component written (INV-4)."""
        day = _day(slot.start, ctx)
        contracted = self.price.grid.capacity_at(day).contracted
        kw = None if contracted is None else max(limit.limit_kw for limit in contracted.limits)
        rate = vat_at(self.price.state, day, kw)
        base = sum(
            (value for name, value in slot.components.items() if name in self.taxed),
            Decimal(0),
        )
        return with_component(slot, self.component, rate * base)


@dataclass(frozen=True, slots=True)
class GridShare:
    """What the copy adds to a slot on top of the energy: grid, its levies, their VAT.

    The synthesising forecaster's grid charge (D1 §5.5): the known head's energy is
    its total less this, and the tail adds it back, so the two sit level.
    """

    stages: tuple[PriceModifier, ...]

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the copy's share of the price at `when`."""
        slot = Slot(
            start=when, end=when, total=Decimal(0), components={}, confidence=Confidence.KNOWN
        )
        for stage in self.stages:
            slot = stage.apply(slot, ctx)
        return slot.total


def chain(
    price: HouseholdPrice,
    modifiers: Sequence[PriceModifier],
    source_basis: frozenset[str],
) -> tuple[tuple[PriceModifier, ...], GridShare]:
    """Return the site's chain by party and the forecaster's grid share (D1 §5.3).

    `modifiers` are the household's add-ons, already built in their stored order
    with a price-replacing one first (`registry.chain_from`); after migration none
    of them is `vat` or `levy`, which are the state stage's (D13 §10, O4).
    """
    supplier = [m for m in modifiers if PARTY.get(m.component) is Party.SUPPLIER]
    grid_typed = [m for m in modifiers if PARTY.get(m.component) is Party.GRID]
    schemes = [m for m in modifiers if PARTY.get(m.component) is Party.STATE]

    grid: list[PriceModifier] = []
    if "grid" not in source_basis:
        grid += grid_typed
        if price.grid.energy:
            grid.append(GridEnergy(price))
    levies: list[PriceModifier] = [] if "levies" in source_basis else [StateLevies(price)]

    taxed = {GRID_ENERGY, LEVY, SUBSIDY}
    if "vat" not in source_basis:
        taxed.add(SPOT)
    if not price.supplier.basis.vat:
        taxed |= {"supplier", "tier", "day_type"}
    vat = StateVat(price, frozenset(taxed))
    share = GridShare(stages=(*grid, *levies, vat))
    return (*supplier, *grid, *levies, *schemes, vat), share
