"""The chain by party - every component counted once, taxes at the slot's date.

D1 §9 19 and D13 §19 10 (INV-72): every price-source basis × a copy stored excl.
and incl. VAT composes to the same price, no component twice and none missing,
and one Tensio TS month is composed and billed identically from both copies.
D1 §9 20 (D13 §18 G16): the state stage takes each slot's own date's rates.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.model import Confidence, Money, Slot
from custom_components.powerplan.core.pricing import party
from custom_components.powerplan.core.pricing.modifiers.base import GRID_ENERGY, SPOT
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.tariffs import Evaluator, StepTable, TimeFilter
from custom_components.powerplan.core.tariffs.household import (
    EXCL,
    EnergyPeriod,
    EnergyVersion,
    HouseholdPrice,
    LoadTariff,
    Party,
    SwitchedWindow,
    TaxZone,
    from_json,
    from_preset,
    published_levies_at,
    spec,
    to_json,
)
from custom_components.powerplan.core.tariffs.rules import loader
from tests.builders.curves import OSLO, context, no3_shape
from tests.builders.presets import fixture_raw
from tests.core.tariffs.conftest import Holidays, closed

if TYPE_CHECKING:
    from custom_components.powerplan.core.pricing.modifiers.base import PriceModifier

NO = TaxZone(country="NO")


def tensio(zone: TaxZone = NO) -> HouseholdPrice:
    """Tensio TS's WP4.6 copy: published incl. VAT, forbruksavgift and Enova (D-0523)."""
    return from_preset(fixture_raw("no/tensio-ts"), source="shipped", zone=zone)


def excl(price: HouseholdPrice) -> HouseholdPrice:
    """Return the same tariff as a source publishing excl. VAT and levies would store it.

    Each figure is Tensio's own less Norway's VAT and levies on its version's date,
    exactly - so the two copies are one tariff, published two ways.
    """
    vat = Decimal("1.25")

    def net(version: EnergyVersion) -> EnergyVersion:
        levies = published_levies_at(price.state, version.valid_from, price.grid.basis)
        return replace(
            version,
            periods=tuple(
                replace(period, price=period.price / vat - levies) for period in version.periods
            ),
        )

    capacity = []
    for version in price.grid.capacity:
        peak = version.peak
        assert peak is not None
        assert isinstance(peak.pricing, StepTable)
        steps = tuple(
            replace(
                step,
                fee_per_period=Money(
                    step.fee_per_period.amount / vat, step.fee_per_period.currency
                ),
            )
            for step in peak.pricing.steps
        )
        capacity.append(replace(version, rules=(replace(peak, pricing=StepTable(steps)),)))
    grid = replace(
        price.grid,
        basis=EXCL,
        energy=tuple(net(version) for version in price.grid.energy),
        capacity=tuple(capacity),
    )
    return replace(price, grid=grid)


def spot_slot(start: datetime, value: Decimal, minutes: int = 60) -> Slot:
    """Return a slot holding only the price source's own value."""
    return Slot(
        start=start,
        end=start + timedelta(minutes=minutes),
        total=value,
        components={SPOT: value},
        confidence=Confidence.KNOWN,
    )


def compose(slot: Slot, chain: tuple[PriceModifier, ...], when: datetime | None = None) -> Slot:
    """Run `chain` over `slot`, as `build_curve` does."""
    ctx = context(when or slot.start)
    for modifier in chain:
        slot = modifier.apply(slot, ctx)
    return slot


# --------------------------------------------------------------------------- #
# D1 §9 19, D13 §19 10 - counted once
# --------------------------------------------------------------------------- #

BASES = [
    frozenset({"spot"}),
    frozenset({"spot", "vat"}),
    frozenset({"spot", "levies"}),
    frozenset({"spot", "grid"}),
    frozenset({"spot", "grid", "vat"}),
    frozenset({"spot", "grid", "levies", "vat"}),  # stromligning, a supplier's total
]


@pytest.mark.inv("INV-72")
@pytest.mark.parametrize(
    ("basis", "stored"),
    list(itertools.product(BASES, ("incl", "excl"))),
    ids=lambda value: "+".join(sorted(value)) if isinstance(value, frozenset) else value,
)
def test_19_every_source_basis_and_copy_counts_each_component_once(
    basis: frozenset[str], stored: str
) -> None:
    """What the source already has is not added; what it lacks is added once."""
    price = tensio() if stored == "incl" else excl(tensio())
    chain, _ = party.chain(price, (), basis)
    start = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)  # 18:00 in Oslo: the day rate
    spot = Decimal("0.80")  # excl. VAT
    grid = Decimal("0.3779") / Decimal("1.25") - Decimal("0.0813")  # Tensio's day, net
    levies = Decimal("0.0813")  # forbruksavgift 7.13 + Enova 1.0 øre, 2026

    published = spot + (grid if "grid" in basis else 0) + (levies if "levies" in basis else 0)
    source = published * (Decimal("1.25") if "vat" in basis else 1)
    slot = compose(spot_slot(start, source), chain)

    assert slot.total == (spot + grid + levies) * Decimal("1.25")
    assert (GRID_ENERGY in slot.components) is ("grid" not in basis)
    assert ("levy" in slot.components) is ("levies" not in basis)
    assert sum(slot.components.values()) == slot.total
    assert {party.party_of(name) for name in slot.components} <= set(Party)


@pytest.mark.inv("INV-72")
def test_10_one_tensio_month_is_composed_identically_from_both_copies() -> None:
    """Every hour of September 2026: the same components, the same total."""
    incl, net = tensio(), excl(tensio())
    chains = [party.chain(price, (), frozenset({"spot"}))[0] for price in (incl, net)]
    start = datetime(2026, 9, 1, tzinfo=OSLO).astimezone(UTC)
    for hour in range(30 * 24):
        when = start + timedelta(hours=hour)
        slot = spot_slot(when, no3_shape(when.astimezone(OSLO)))
        a, b = (compose(slot, chain) for chain in chains)
        assert a.components == b.components, when
        assert a.total == b.total


@pytest.mark.inv("INV-72")
def test_10_one_tensio_month_is_billed_identically_from_both_copies() -> None:
    """The capacity fee a household pays does not depend on how its copy was published."""
    bills = []
    for price in (tensio(), excl(tensio())):
        evaluator = Evaluator(spec(price), tz=OSLO, calendar=Holidays())
        for day, kw in ((3, 4.2), (11, 6.8), (19, 5.1), (24, 3.9)):
            evaluator.record_window(closed(datetime(2026, 9, day, 18), kw))
        bills.append(evaluator.bill(evaluator.period(datetime(2026, 9, 20, tzinfo=OSLO))))
    assert bills[0].capacity_fee == bills[1].capacity_fee
    assert bills[0].level == bills[1].level
    # Tensio's 2026-07-01 table, 5–10 kW: 397 kr incl. VAT (the sheet's own figure).
    assert bills[0].capacity_fee.amount == 397


def test_a_nord_norge_house_pays_tensios_figures_without_vat() -> None:
    """A copy published incl. the national VAT is paid at the zone's (§9.1, mval. § 6-6)."""
    nord = tensio(TaxZone(country="NO", key="nord"))
    chain, _ = party.chain(nord, (), frozenset({"spot"}))
    slot = compose(spot_slot(datetime(2026, 9, 15, 16, tzinfo=UTC), Decimal("0.80")), chain)
    assert slot.components["vat"] == 0
    assert slot.total == Decimal("0.80") + Decimal("0.3779") / Decimal("1.25")
    fees = spec(nord).versions[-1].peak
    assert fees is not None
    assert isinstance(fees.pricing, StepTable)
    assert fees.pricing.steps[2].fee_per_period.amount == Decimal(397) / Decimal("1.25")


# --------------------------------------------------------------------------- #
# D1 §9 20 - the state stage by date
# --------------------------------------------------------------------------- #


def uk_site() -> HouseholdPrice:
    """Return a GB house on the national no-peak rule."""
    return from_preset(
        loader.load_raw("uk/nopeak") | {"currency": "GBP"},
        source="template",
        zone=TaxZone(country="GB"),
        typed=True,
    )


@pytest.mark.inv("INV-71")
def test_20_a_gb_site_pays_five_percent_then_none_without_a_reconfigure() -> None:
    """The zero rate from 2026-10-01 applies on its date, from the module (G16)."""
    chain, _ = party.chain(uk_site(), (), frozenset({"spot"}))
    london = datetime(2026, 9, 30, 12, tzinfo=UTC)
    before = compose(spot_slot(london, Decimal("0.20")), chain)
    after = compose(spot_slot(london + timedelta(days=1), Decimal("0.20")), chain)
    assert before.components["vat"] == Decimal("0.0100")
    assert after.components["vat"] == 0


@pytest.mark.inv("INV-71")
def test_20_a_levy_changed_on_the_first_of_january_prices_each_month_at_its_own() -> None:
    """Forbruksavgift 12.53 øre in December 2025, 7.13 in January 2026, Enova 1.0 in both."""
    chain, _ = party.chain(tensio(), (), frozenset({"spot"}))
    december = datetime(2025, 12, 31, 12, tzinfo=UTC)
    january = datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert compose(spot_slot(december, Decimal(1)), chain).components["levy"] == Decimal("0.1353")
    assert compose(spot_slot(january, Decimal(1)), chain).components["levy"] == Decimal("0.0813")


def test_the_parties_of_a_norgespris_slot() -> None:
    """Norgespris is the supplier's; the grid charge the grid's; VAT and levies the state's."""
    chain, _ = party.chain(tensio(), (FixedPrice(price=Decimal("0.40")),), frozenset({"spot"}))
    slot = compose(spot_slot(datetime(2026, 9, 15, 16, tzinfo=UTC), Decimal("2.10")), chain)
    by_party: dict[Party, Decimal] = {}
    for name, value in slot.components.items():
        by_party[party.party_of(name)] = by_party.get(party.party_of(name), Decimal(0)) + value
    assert by_party[Party.SUPPLIER] == Decimal("0.40")
    assert by_party[Party.GRID] == Decimal("0.3779") / Decimal("1.25") - Decimal("0.0813")
    assert slot.total == Decimal("0.50") + Decimal("0.3779"), "0.40 × 1.25 + Tensio's day charge"


def test_the_forecasters_grid_share_is_what_the_copy_adds() -> None:
    """The synthesised tail adds back exactly what the known head's energy is net of."""
    chain, share = party.chain(tensio(), (), frozenset({"spot"}))
    when = datetime(2026, 9, 15, 16, tzinfo=UTC)
    slot = compose(spot_slot(when, Decimal("0.80")), chain)
    assert slot.total - share.price_at(when, context(when)) == Decimal("0.80") * Decimal("1.25")
    assert share.price_at(when, context(when)) == Decimal("0.3779")


def test_a_copy_with_no_energy_charge_adds_no_grid_component() -> None:
    """A capacity-only copy puts nothing on the grid's energy line."""
    price = tensio()
    bare = replace(price, grid=replace(price.grid, energy=()))
    chain, _ = party.chain(bare, (), frozenset({"spot"}))
    slot = compose(spot_slot(datetime(2026, 9, 15, 16, tzinfo=UTC), Decimal(1)), chain)
    assert GRID_ENERGY not in slot.components


def test_the_tail_after_a_copy_without_an_energy_charge_counts_its_levies_once() -> None:
    """A capacity-only copy: the share is levies and VAT; the synthesised tail adds them once."""
    from custom_components.powerplan.core.model import (  # noqa: PLC0415
        Carrier,
        Direction,
        PriceCurve,
    )
    from custom_components.powerplan.core.pricing.forecasters.synthesised import (  # noqa: PLC0415
        Synthesised,
    )

    price = tensio()
    bare = replace(price, grid=replace(price.grid, energy=()))
    chain, share = party.chain(bare, (), frozenset({"spot"}))
    start = datetime(2026, 9, 15, 10, tzinfo=UTC)
    known = compose(spot_slot(start, Decimal("0.80")), chain)
    curve = PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=(known,),
        built_at=start,
        sources=("test",),
    )
    tail = Synthesised(tou=share, slot_minutes=60).extend(
        curve, start + timedelta(hours=3), context(start), ()
    )
    assert tail.slots[-1].total == known.total, "same spot, same taxes: the same price"
    assert "levy" in tail.slots[-1].components


# --------------------------------------------------------------------------- #
# D13 §18 G22 - a grid charge on a share of spot
# --------------------------------------------------------------------------- #


def _loss_share(price: HouseholdPrice) -> HouseholdPrice:
    """Tensio's copy with a 5 % share of spot on its energy charge (Kraftringen's rule)."""
    energy = tuple(replace(v, spot_share=Decimal("0.05")) for v in price.grid.energy)
    return replace(price, grid=replace(price.grid, energy=energy))


@pytest.mark.inv("INV-72")
def test_g22_a_spot_share_is_added_to_the_grid_charge_once() -> None:
    """Spot 1.20 NOK: the grid adds 0.06 on top of its own rate, and VAT taxes it once."""
    price = _loss_share(excl(tensio()))
    when = datetime(2026, 9, 24, 10, tzinfo=UTC)
    plain = compose(
        spot_slot(when, Decimal("1.20")), party.chain(excl(tensio()), (), frozenset({"spot"}))[0]
    )
    shared = compose(
        spot_slot(when, Decimal("1.20")), party.chain(price, (), frozenset({"spot"}))[0]
    )
    added = shared.components[GRID_ENERGY] - plain.components[GRID_ENERGY]
    assert added == Decimal("0.06")
    assert shared.total - plain.total == Decimal("0.06") * Decimal("1.25")


def test_g22_a_spot_quoted_with_vat_is_shared_without_it() -> None:
    """A price source that includes VAT: the share is of spot excl. VAT."""
    price = _loss_share(excl(tensio()))
    when = datetime(2026, 9, 24, 10, tzinfo=UTC)
    basis = frozenset({"spot", "vat"})
    plain = compose(spot_slot(when, Decimal("1.25")), party.chain(excl(tensio()), (), basis)[0])
    shared = compose(spot_slot(when, Decimal("1.25")), party.chain(price, (), basis)[0])
    assert shared.components[GRID_ENERGY] - plain.components[GRID_ENERGY] == Decimal("0.05")


# --------------------------------------------------------------------------- #
# D1 §9 23 (G13) - a load on its own grid tariff
# --------------------------------------------------------------------------- #


def _modul3(price: HouseholdPrice) -> HouseholdPrice:
    """Tensio with a heat-pump tariff: 0.10 at night (22–06), 0.30 by day, as published."""
    night = EnergyPeriod(when=TimeFilter(hours=((22 * 60, 6 * 60),)), price=Decimal("0.10"))
    own = LoadTariff(
        key="modul3",
        name="Varmepumpe",
        energy=(EnergyVersion(date(2026, 1, 1), (night,), Decimal("0.30")),),
    )
    return replace(price, grid=replace(price.grid, per_load=(own,)))


def test_23_a_loads_curve_carries_its_own_grid_component_and_the_houses_does_not_change() -> None:
    """The house composes as before; the load's grid line is its own, the rest identical."""
    price = _modul3(tensio())
    house, _ = party.chain(price, (), frozenset({"spot"}))
    plain, _ = party.chain(tensio(), (), frozenset({"spot"}))
    load = party.chain_for_load(price, "modul3", (), frozenset({"spot"}))
    assert load is not None
    assert party.chain_for_load(price, "none", (), frozenset({"spot"})) is None
    for hour, published in ((2, Decimal("0.10")), (12, Decimal("0.30"))):
        when = datetime(2026, 9, 24, hour, tzinfo=OSLO)
        slot = spot_slot(when, Decimal("1.00"))
        assert compose(slot, house) == compose(slot, plain)
        own = compose(slot, load)
        levies = published_levies_at(price.state, when.date(), price.grid.basis)
        assert own.components[GRID_ENERGY] == published / Decimal("1.25") - levies
        assert own.components[SPOT] == compose(slot, house).components[SPOT]
        assert own.components["vat"] == (own.total - own.components["vat"]) * Decimal("0.25")


def test_23_per_load_tariffs_and_switched_windows_survive_the_copy() -> None:
    """`to_json` → `from_json` keeps both, and an old copy without them reads as none."""
    hdo = SwitchedWindow(
        key="A1B4DP6", name="HDO", windows=(TimeFilter(hours=((0, 180),), weekdays=(0, 1)),)
    )
    price = _modul3(tensio())
    price = replace(price, grid=replace(price.grid, switched=(hdo,)))
    again = from_json(to_json(price))
    assert again.grid.per_load == price.grid.per_load
    assert again.grid.switched == (hdo,)
    assert from_json(to_json(tensio())).grid.per_load == ()
