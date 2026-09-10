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

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, Final

from ..model import Confidence
from ..tariffs import countries
from ..tariffs.household import (
    HouseholdPrice,
    Party,
    band_vat_at,
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
    "StateScheme",
    "StateVat",
    "chain",
    "chain_for_load",
    "party_of",
    "split",
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


def split(components: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Return a slot's components summed by party - what D11 and D12 split by (INV-72)."""
    parts: dict[str, Decimal] = {}
    for name, value in components.items():
        party = PARTY[name].value
        parts[party] = parts.get(party, Decimal(0)) + value
    return parts


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
    #: The price source quotes spot with VAT: a spot share is taken of spot without it.
    spot_incl_vat: bool = False

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
        """Return `slot` with the grid energy component written (INV-4).

        A version with a `spot_share` adds that share of the slot's spot price,
        excl. VAT (D13 §18 G22).
        """
        charge = self.price_at(slot.start, ctx)
        day = _day(slot.start, ctx)
        version = self.price.grid.energy_at(day)
        if version is not None and version.spot_share:
            spot = slot.components.get(SPOT, Decimal(0))
            if self.spot_incl_vat:
                spot /= Decimal(1) + vat_at(self.price.state, day)
            charge += spot * version.spot_share
        return with_component(slot, self.component, charge)


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
        # Portugal's first 200 kWh of the month at 6 % (G17): the running total at
        # the slot's start decides, the boundary belonging to the band above.
        band = band_vat_at(self.price, day, kw, ctx.mtd_kwh_at(slot.start))
        rate = vat_at(self.price.state, day, kw) if band is None else band
        base = sum(
            (value for name, value in slot.components.items() if name in self.taxed),
            Decimal(0),
        )
        return with_component(slot, self.component, rate * base)


@dataclass(frozen=True, slots=True)
class StateScheme:
    """A scheme the household is in: the state pays `share` of the spot above a dated threshold.

    Norway's strømstøtte (D13 §4 party 3), from the country module at the slot's
    date; the household types nothing. Written as `subsidy`, before VAT, as the
    add-on it replaces was (D-0551).
    """

    key: ClassVar[str] = "state_scheme"
    component: ClassVar[str] = SUBSIDY
    schema: ClassVar[Schema] = ()

    price: HouseholdPrice
    scheme: str

    def apply(self, slot: Slot, ctx: PriceContext) -> Slot:
        """Return `slot` with the subsidy component written (INV-4)."""
        module = countries.get(self.price.state.zone.country)
        found = None if module is None else module.scheme(self.scheme)
        rate = None if found is None else countries.pick(found.threshold, _day(slot.start, ctx))
        if found is None or rate is None:
            return with_component(slot, self.component, Decimal(0))
        above = slot.components.get(SPOT, Decimal(0)) - rate.value
        return with_component(
            slot, self.component, -found.share * above if above > 0 else Decimal(0)
        )


@dataclass(frozen=True, slots=True)
class GridShare:
    """What the copy adds to a slot on top of the energy: grid, its levies, their VAT.

    The synthesising forecaster's grid charge (D1 §5.5): the known head's energy is
    its total less this, and the tail adds it back, so the two sit level.
    """

    stages: tuple[PriceModifier, ...]

    def components_at(self, when: datetime, ctx: PriceContext) -> Mapping[str, Decimal]:
        """Return the copy's share at `when`, component by component - each keeps its party."""
        slot = Slot(
            start=when, end=when, total=Decimal(0), components={}, confidence=Confidence.KNOWN
        )
        for stage in self.stages:
            slot = stage.apply(slot, ctx)
        return slot.components

    def price_at(self, when: datetime, ctx: PriceContext) -> Decimal:
        """Return the copy's share of the price at `when`."""
        return sum(self.components_at(when, ctx).values(), Decimal(0))


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
            grid.append(GridEnergy(price, spot_incl_vat="vat" in source_basis))
    levies: list[PriceModifier] = [] if "levies" in source_basis else [StateLevies(price)]

    module = countries.get(price.state.zone.country)
    for key in price.state.schemes:
        scheme = None if module is None else module.scheme(key)
        # A scheme the supplier's kind excludes (strømstøtte beside Norgespris) is never paid.
        if scheme is not None and price.supplier.kind not in scheme.excludes:
            schemes.append(StateScheme(price, key))

    taxed = {GRID_ENERGY, LEVY, SUBSIDY}
    if "vat" not in source_basis:
        taxed.add(SPOT)
    if not price.supplier.basis.vat:
        taxed |= {"supplier", "tier", "day_type"}
    vat = StateVat(price, frozenset(taxed))
    share = GridShare(stages=(*grid, *levies, vat))
    return (*supplier, *grid, *levies, *schemes, vat), share


def chain_for_load(
    price: HouseholdPrice,
    key: str,
    modifiers: Sequence[PriceModifier],
    source_basis: frozenset[str],
) -> tuple[PriceModifier, ...] | None:
    """Return the chain of a load on its own grid tariff, `None` where the copy has none (G13).

    The same chain with that tariff's grid component in place of the house's: the
    household's own grid-party add-ons are the house's meter and are left out;
    supplier and state stay as they are (D1 §5.3, D13 §18 G13).
    """
    grid = price.grid.for_load(key)
    if grid is None:
        return None
    others = [m for m in modifiers if PARTY.get(m.component) is not Party.GRID]
    return chain(replace(price, grid=grid), others, source_basis)[0]
