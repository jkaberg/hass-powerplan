"""D6 §9 13 - zones: the cheapest kilowatt-hour of **heat** wins (INV-42).

A zone is one space with more than one way to heat it, and the decision is never
"which load is cheaper to run" but "which source is cheaper per kWh of heat":

    cost(source) = price(carrier, now) / efficiency(source, T_out)

which is why COP **inverts** priority. A heat pump at COP 3 delivers three
kilowatt-hours of heat per kilowatt-hour drawn where the slab delivers one, so the
pump is kept and the slab is substituted out - shedding the pump to protect the
slab trades 3 kW of heat for 2 kW and is strictly backwards.

Three guards stop that arithmetic doing damage: substitution never engages a heat
pump below `min_cop` (2.0) - a pump barely better than a resistor, in the weather
where it defrosts, is not a substitution target; a zone with a single source never
substitutes at all; and a bathroom keeps its own source, whatever the ranking says.
Across carriers the gas boiler and the pump land within a few per cent of each
other at realistic prices, which is why the switch needs a 15 % margin, a
confirmation and a dwell - a boiler that flaps is a boiler that fails.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.allocation import (
    AllocCfg,
    AllocState,
    CircuitLimit,
    ShedReason,
    Zone,
    ZoneChoice,
    ZoneSource,
    allocate,
)
from custom_components.powerplan.core.loads.stores.cop import DEFAULT_COP_CURVES, CopCurve
from custom_components.powerplan.core.model import Carrier
from tests.core.allocation.conftest import (
    NOW,
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    comfort,
    curve_of,
    demand,
    ev_view,
    loop_view,
    pump_view,
    state_with,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.powerplan.core.model import PriceCurve
    from custom_components.powerplan.core.strategies import LoadView

#: Resistive heat: one kilowatt-hour in, one kilowatt-hour of heat out.
RESISTIVE = CopCurve.flat(1.0)

#: A condensing gas boiler (D6 §5.7's worked example).
BOILER = CopCurve.flat(0.95)


def _slab(**kwargs: Any) -> LoadView:
    """Return the living-room slab: 2 kW of resistive cable under a thermostat."""
    options: dict[str, Any] = {
        "load_id": "slab",
        "priority": 30,
        "nameplate_w": 2000.0,
        "demand": demand(max_w=2000.0, comfort=comfort(current=21.0, target=23.0, floor=18.0)),
    }
    options.update(kwargs)
    return loop_view(**options)


def _room(**kwargs: Any) -> LoadView:
    """Return a hydronic room thermostat: it asks for heat and draws nothing (D4 §5.15)."""
    options: dict[str, Any] = {
        "load_id": "room",
        "priority": 30,
        "nameplate_w": 0.0,
        "demand": demand(max_w=0.0, comfort=comfort(current=21.0, target=23.0, floor=18.0)),
    }
    options.update(kwargs)
    return loop_view(**options)


def _boiler(**kwargs: Any) -> LoadView:
    """Return the gas boiler: 14 kW of heat for about 90 W of pump and fan."""
    options: dict[str, Any] = {
        "load_id": "boiler",
        "priority": 45,
        "nameplate_w": 90.0,
        "carrier": Carrier.GAS,
        "demand": demand(max_w=90.0, reason="gas heat"),
    }
    options.update(kwargs)
    return loop_view(**options)


def _prices(electricity: str = "0.35", gas: str | None = None) -> Mapping[Carrier, PriceCurve]:
    """Return the import curves a zone prices its sources with (D1 §4)."""
    curves = {Carrier.ELECTRICITY: curve_of(electricity)}
    if gas is not None:
        curves[Carrier.GAS] = curve_of(gas, carrier=Carrier.GAS)
    return curves


def _electric_zone(*, outdoor_c: float = 0.0, cop: CopCurve | None = None, **kwargs: Any) -> Zone:
    """Return the slab + heat pump pair - the pyscript's `substitution.pairs`."""
    options: dict[str, Any] = {
        "key": "zone_living",
        "members": frozenset({"slab"}),
        "sources": (
            ZoneSource(load_id="slab", efficiency=RESISTIVE),
            ZoneSource(load_id="pump", efficiency=cop or DEFAULT_COP_CURVES["a2a"]),
        ),
        "prices": _prices(),
        "outdoor_c": outdoor_c,
    }
    options.update(kwargs)
    return Zone(**options)


def _hybrid(*, electricity: str, gas: str = "0.12", cop: CopCurve, **kwargs: Any) -> Zone:
    """Return the cross-carrier pair: a hybrid heat pump beside a gas boiler."""
    options: dict[str, Any] = {
        "key": "zone_house",
        "members": frozenset({"room"}),
        "sources": (
            ZoneSource(load_id="pump", efficiency=cop),
            ZoneSource(load_id="boiler", carrier=Carrier.GAS, efficiency=BOILER, heat_w=14_000.0),
        ),
        "prices": _prices(electricity, gas),
        "outdoor_c": 0.0,
    }
    options.update(kwargs)
    return Zone(**options)


def _hybrid_house() -> list[LoadView]:
    """Return the hybrid house: a pump, a boiler and a room that asks for heat."""
    return [pump_view(), _boiler(), _room()]


# --------------------------------------------------------------------------- #
# The COP inversion
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-42")
def test_13_cop_inverts_priority_the_pump_is_kept_and_the_slab_substituted() -> None:
    """COP 3.0 against η 1.0 on the same price: the pump carries the room."""
    ctx = alloc_ctx([pump_view(), _slab()], budget=budget_of(11_500.0))

    grants, report, _state = allocate(ctx, (_electric_zone(),), AllocCfg(), AllocState())

    assert grants["pump"].w > 0.0
    assert grants["pump"].shed is False
    assert grants["slab"].w == 0.0
    assert grants["slab"].shed is True
    assert report.shed_reason["slab"] == ShedReason.ZONE_SUBSTITUTED
    zone = report.zones["zone_living"]
    assert zone.chosen == ("pump",)
    assert zone.substituted == ("slab",)
    assert zone.cost_per_kwh_heat["pump"] == pytest.approx(0.35 / 3.0)
    assert zone.cost_per_kwh_heat["slab"] == pytest.approx(0.35)


@pytest.mark.inv("INV-42")
def test_13_substitution_disengages_below_the_cop_floor() -> None:
    """At −18 °C the A2W pump is at COP 1.6: the slab heats the room, not the pump."""
    ctx = alloc_ctx([pump_view(), _slab()], budget=budget_of(11_500.0))
    zone = _electric_zone(outdoor_c=-18.0, cop=DEFAULT_COP_CURVES["a2w"])

    grants, report, _state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert grants["slab"].w == pytest.approx(2000.0)
    assert grants["pump"].w == 0.0
    assert report.shed_reason["pump"] == ShedReason.ZONE_SUBSTITUTED
    assert report.zones["zone_living"].chosen == ("slab",)
    assert report.zones["zone_living"].excluded["pump"] == "cop_below_floor"


@pytest.mark.inv("INV-42")
def test_13_a_zone_with_one_source_never_substitutes() -> None:
    """Nothing to substitute to: the zone has no opinion at all (INV-42)."""
    zone = Zone(
        key="zone_living",
        members=frozenset({"slab"}),
        sources=(ZoneSource(load_id="slab", efficiency=RESISTIVE),),
        prices=_prices(),
        outdoor_c=0.0,
    )
    ctx = alloc_ctx([_slab()], budget=budget_of(11_500.0))

    grants, report, _state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert grants["slab"].w == pytest.approx(2000.0)
    assert report.shed == ()
    assert report.zones["zone_living"].reason == "single source"


@pytest.mark.inv("INV-42")
def test_13_a_bathroom_keeps_its_own_source() -> None:
    """`never_substitute`: the pump may be cheaper, the bathroom loop still runs."""
    bath = loop_view(demand=demand(max_w=960.0, comfort=comfort(current=22.0, target=24.0)))
    zone = Zone(
        key="zone_bath",
        members=frozenset({"loop_bath"}),
        sources=(
            ZoneSource(load_id="loop_bath", efficiency=RESISTIVE),
            ZoneSource(load_id="pump", efficiency=DEFAULT_COP_CURVES["a2a"]),
        ),
        never_substitute=frozenset({"loop_bath"}),
        prices=_prices(),
        outdoor_c=0.0,
    )
    ctx = alloc_ctx([pump_view(), bath], budget=budget_of(11_500.0))

    grants, report, _state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert grants["loop_bath"].w == pytest.approx(960.0)
    assert grants["loop_bath"].shed is False
    assert report.zones["zone_bath"].substituted == ()
    assert report.zones["zone_bath"].chosen == ("pump",)


@pytest.mark.inv("INV-42")
def test_13_the_cheapest_source_is_topped_up_when_it_cannot_cover_the_demand() -> None:
    """A pump that cannot carry the whole deficit keeps the slab beside it (§5.7)."""
    slab = _slab(demand=demand(max_w=2000.0, comfort=comfort(current=18.0, target=23.0)))
    small_pump = pump_view(nameplate_w=500.0, demand=demand(max_w=500.0, comfort=comfort()))
    zone = Zone(
        key="zone_living",
        members=frozenset({"slab"}),
        sources=(
            ZoneSource(load_id="slab", efficiency=RESISTIVE),
            ZoneSource(load_id="pump", efficiency=CopCurve.flat(3.0), heat_w=1500.0),
        ),
        prices=_prices(),
        outdoor_c=0.0,
    )
    ctx = alloc_ctx([small_pump, slab], budget=budget_of(11_500.0))

    grants, report, _state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_living"].chosen == ("pump", "slab")
    assert report.zones["zone_living"].demand_w == pytest.approx(2000.0)
    assert grants["slab"].w == pytest.approx(2000.0)
    assert grants["pump"].w > 0.0


# --------------------------------------------------------------------------- #
# Cross-carrier: hysteresis, confirmation and dwell
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-42")
def test_13_a_cross_carrier_tie_keeps_the_current_source() -> None:
    """0.125 against 0.1263 €/kWh heat is a tie; the dwell keeps the boiler (§5.7)."""
    zone = _hybrid(
        electricity="0.35",
        cop=CopCurve.flat(2.8),
        choice=ZoneChoice(source="boiler", since=NOW - timedelta(hours=2)),
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0))

    grants, report, state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("boiler",)
    assert grants["pump"].w == 0.0
    assert report.shed_reason["pump"] == ShedReason.ZONE_SUBSTITUTED
    assert state.zone_choice["zone_house"].source == "boiler"
    assert state.zone_choice["zone_house"].since == NOW - timedelta(hours=2)


@pytest.mark.inv("INV-42")
def test_13_a_cheaper_source_waits_for_the_dwell() -> None:
    """Gas is 69 % cheaper, but the pump has run five minutes: no switch yet."""
    zone = _hybrid(
        electricity="0.90",
        cop=CopCurve.flat(2.2),
        choice=ZoneChoice(source="pump", since=NOW - timedelta(minutes=5)),
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0))

    grants, report, state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("pump",)
    assert grants["boiler"].w == 0.0
    assert report.shed_reason["boiler"] == ShedReason.ZONE_SUBSTITUTED
    assert state.zone_choice["zone_house"].source == "pump"
    assert state.zone_choice["zone_house"].candidate == "boiler"
    assert state.zone_choice["zone_house"].candidate_since == NOW


@pytest.mark.inv("INV-42")
def test_13_a_cheaper_source_waits_to_be_confirmed() -> None:
    """Past the dwell but seen cheaper only for a minute: still the pump (§5.7)."""
    zone = _hybrid(
        electricity="0.90",
        cop=CopCurve.flat(2.2),
        choice=ZoneChoice(
            source="pump",
            since=NOW - timedelta(hours=2),
            candidate="boiler",
            candidate_since=NOW - timedelta(seconds=60),
        ),
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0))

    _grants, report, state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("pump",)
    assert state.zone_choice["zone_house"].source == "pump"
    assert state.zone_choice["zone_house"].candidate_since == NOW - timedelta(seconds=60)


@pytest.mark.inv("INV-42")
def test_13_gas_wins_once_the_margin_is_confirmed_and_the_dwell_is_out() -> None:
    """0.409 against 0.126 €/kWh heat, confirmed for two planning cycles: gas."""
    zone = _hybrid(
        electricity="0.90",
        cop=CopCurve.flat(2.2),
        choice=ZoneChoice(
            source="pump",
            since=NOW - timedelta(hours=2),
            candidate="boiler",
            candidate_since=NOW - timedelta(minutes=31),
        ),
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0))

    grants, report, state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("boiler",)
    assert grants["pump"].w == 0.0
    assert report.shed_reason["pump"] == ShedReason.ZONE_SUBSTITUTED
    assert state.zone_choice["zone_house"].source == "boiler"
    assert state.zone_choice["zone_house"].since == NOW
    assert state.zone_choice["zone_house"].candidate is None


@pytest.mark.inv("INV-42")
def test_13_an_unavailable_source_falls_to_the_next_cheapest_at_once() -> None:
    """No dwell for a source that cannot run: the pump is below its COP floor (D6 §8)."""
    zone = _hybrid(
        electricity="0.90",
        cop=CopCurve.flat(1.6),
        choice=ZoneChoice(source="pump", since=NOW - timedelta(minutes=1)),
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0))

    _grants, report, state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("boiler",)
    assert report.zones["zone_house"].excluded["pump"] == "cop_below_floor"
    assert state.zone_choice["zone_house"].source == "boiler"


@pytest.mark.inv("INV-42")
def test_13_a_source_whose_carrier_is_not_priced_is_not_chosen() -> None:
    """A gas boiler on a site with no gas curve cannot be costed (D6 §8)."""
    shape = _hybrid(electricity="0.90", cop=CopCurve.flat(2.2))
    priceless = Zone(
        key="zone_house",
        members=shape.members,
        sources=shape.sources,
        prices=_prices("0.90"),
        outdoor_c=0.0,
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0))

    grants, report, _state = allocate(ctx, (priceless,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("pump",)
    assert report.zones["zone_house"].excluded["boiler"] == "no_price"
    assert grants["pump"].w > 0.0


@pytest.mark.inv("INV-42")
def test_13_at_stage_two_the_capacity_penalty_flips_a_close_call_to_gas() -> None:
    """Substitution "in" at stage 2 is the ceiling buying the non-electric source."""
    zone = _hybrid(
        electricity="0.35",
        cop=CopCurve.flat(2.8),
        choice=ZoneChoice(source="pump", since=NOW - timedelta(minutes=1)),
        capacity_penalty=Decimal("1.00"),
    )
    ctx = alloc_ctx(_hybrid_house(), budget=budget_of(11_500.0), stage=2)

    _grants, report, state = allocate(ctx, (zone,), AllocCfg(), AllocState())

    assert report.zones["zone_house"].chosen == ("boiler",)
    assert report.zones["zone_house"].reason == "capacity penalty"
    assert state.zone_choice["zone_house"].source == "boiler"


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-42", "INV-60")
def test_13_a_substituted_member_frees_its_share_of_its_circuit() -> None:
    """The zone frees the slab's share of its circuit for the charger.

    The slab and the charger share a 16 A fuse; substituting the slab out hands the
    charger the whole circuit, because the circuit counts reservations and the slab
    no longer holds one.
    """
    ev = ev_view()
    circuit = CircuitLimit(
        key="circuit_hall", limit_w=16.0 * W_PER_AMP, members=frozenset({"slab", "ev"})
    )
    ctx = alloc_ctx([pump_view(), _slab(), ev], budget=budget_of(11_500.0))

    grants, report, _state = allocate(ctx, (circuit, _electric_zone()), AllocCfg(), AllocState())

    assert report.shed_reason["slab"] == ShedReason.ZONE_SUBSTITUTED
    assert grants["ev"].w == pytest.approx(16.0 * W_PER_AMP)
    assert grants["ev"].capped_by == ("circuit_hall",)


@pytest.mark.inv("INV-42")
def test_13_a_comfort_violator_is_never_substituted_away() -> None:
    """A violated floor is item 3, a zone's preference item 5 (INV-1, D6 §5.3)."""
    cold_slab = _slab(
        demand=demand(max_w=2000.0, comfort=comfort(current=17.0, target=23.0, violated=True))
    )
    ctx = alloc_ctx([pump_view(), cold_slab], budget=budget_of(11_500.0))

    grants, _report, _state = allocate(ctx, (_electric_zone(),), AllocCfg(), AllocState())

    assert grants["slab"].w == pytest.approx(2000.0)
    assert grants["slab"].shed is False


def test_13_the_zone_choice_round_trips_through_the_state() -> None:
    """`AllocState` carries the dwell and the candidate, JSON and back (D6 §7)."""
    state = state_with(
        zone_choice={
            "zone_house": ZoneChoice(
                source="boiler",
                since=NOW,
                candidate="pump",
                candidate_since=NOW - timedelta(minutes=2),
            )
        }
    )

    restored = AllocState.from_dict(state.as_dict())

    assert restored.zone_choice == state.zone_choice
    assert isinstance(state.as_dict()["zone_choice"]["zone_house"]["since"], str)
