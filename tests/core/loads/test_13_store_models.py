"""D4 §9 13 - the store models: two numbers from the design, and the signs (INV-56).

The two numbers D4 §4.3 states outright:

* a 57.5 m² slab under 50 mm of screed is **1.58 kWh/K** (ρ 2200, cp 0.9);
* a 300 L tank from 45 to 75 °C at η 0.98 is **≈ 10.7 kWh**.

Everything else here is the direction-agnosticism the HLD asks for - heating and
cooling are one model with the sign flipped - and INV-56: every store has a
maximum, and nothing asks for energy beyond it.

The `TankStore` *type* (`water_heater`, legionella, the sensorless model) is
WP3.3; the store model is §4.3 and this is where its arithmetic is pinned.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads.stores import (
    DEFAULT_COP_CURVES,
    CopCurve,
    EnergyStore,
    RoomStore,
    SlabStore,
    StoreCtx,
    TankStore,
)
from tests.core.loads.conftest import NOW


def ctx(**kwargs: float) -> StoreCtx:
    """Return a store context at `NOW`."""
    return StoreCtx(now=NOW, **kwargs)


def test_13_a_heavy_slab_is_one_and_a_half_kilowatt_hours_per_kelvin() -> None:
    """57.5 m² × 50 mm × 2200 × 0.9 / 3600 = 1.58 kWh/K (D4 §4.3)."""
    store = SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=None, max_c=27.0)
    assert store.capacity_kwh_per_unit() == pytest.approx(1.58, abs=0.01)
    assert store.required_kwh(20.0, 22.0, None, ctx()) == pytest.approx(3.16, abs=0.02)


def test_13b_a_three_hundred_litre_tank_is_ten_point_seven_kilowatt_hours() -> None:
    """300 L × 4.186 × 30 K / 3600 / 0.98 ≈ 10.7 kWh (D4 §4.3, §5.7)."""
    store = TankStore(litres=300.0, standby_loss_w=0.0, max_c=80.0, min_c=45.0)
    assert store.required_kwh(45.0, 75.0, None, ctx()) == pytest.approx(10.7, abs=0.05)


def test_13b2_standby_loss_is_added_over_the_hours_to_the_deadline() -> None:
    """A tank held for six hours has paid its standby loss by the deadline (§5.7)."""
    store = TankStore(litres=300.0, standby_loss_w=60.0, max_c=80.0, min_c=45.0)
    deadline = NOW + timedelta(hours=6)
    assert store.required_kwh(45.0, 75.0, deadline, ctx()) == pytest.approx(10.7 + 0.36, abs=0.05)


@pytest.mark.inv("INV-56")
def test_13c_nothing_asks_for_energy_beyond_the_maximum() -> None:
    """INV-56: the store's own maximum bounds the request, whoever made it."""
    store = SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=None, max_c=27.0)
    assert store.max_level() == pytest.approx(27.0)
    to_the_cap = store.required_kwh(26.0, 27.0, None, ctx())
    past_the_cap = store.required_kwh(26.0, 31.0, None, ctx())
    assert past_the_cap == pytest.approx(to_the_cap)


@pytest.mark.inv("INV-56")
def test_13c2_a_level_already_over_the_maximum_wants_nothing() -> None:
    """A slab over its cap is not a negative demand, it is no demand."""
    store = SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=None, max_c=27.0)
    assert store.required_kwh(28.0, 27.0, None, ctx()) == pytest.approx(0.0)


def test_13d_cooling_flips_the_sign() -> None:
    """Pre-cooling before an afternoon demand window is the same model (HLD §6.4)."""
    heating = RoomStore(
        volume_m3=250.0, heat_loss_w_per_k=None, thermal_mass_kwh_per_k=7.5, max_c=24.0, min_c=17.0
    )
    cooling = RoomStore(
        volume_m3=250.0,
        heat_loss_w_per_k=None,
        thermal_mass_kwh_per_k=7.5,
        max_c=24.0,
        min_c=17.0,
        direction="cool",
    )
    assert heating.required_kwh(20.0, 22.0, None, ctx()) == pytest.approx(15.0)
    assert heating.required_kwh(22.0, 20.0, None, ctx()) == pytest.approx(0.0)
    assert cooling.required_kwh(22.0, 20.0, None, ctx()) == pytest.approx(15.0)
    assert cooling.required_kwh(20.0, 22.0, None, ctx()) == pytest.approx(0.0)


def test_13d2_a_room_stores_three_percent_of_its_volume_per_kelvin() -> None:
    """The effective building mass of light-weight construction (D4 §5.7)."""
    store = RoomStore.from_volume(volume_m3=250.0, max_c=24.0, min_c=17.0)
    assert store.capacity_kwh_per_unit() == pytest.approx(7.5)


def test_13e_loss_and_coast_need_a_fitted_coefficient() -> None:
    """Without `loss_coeff` the loss term is skipped (conservative) and coast is unknown."""
    blind = SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=None, max_c=27.0)
    fitted = SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=120.0, max_c=27.0)
    deadline = NOW + timedelta(hours=8)
    cold = ctx(outdoor_c=-8.0, indoor_c=22.0)

    assert blind.required_kwh(20.0, 22.0, deadline, cold) == pytest.approx(3.16, abs=0.02)
    assert blind.coast_hours(22.0, 21.0, cold) is None

    with_loss = fitted.required_kwh(20.0, 22.0, deadline, cold)
    assert with_loss == pytest.approx(3.16 + 120.0 * 30.0 * 8.0 / 1000.0, abs=0.05)
    coast = fitted.coast_hours(22.0, 21.0, cold)
    assert coast is not None
    assert coast == pytest.approx(1.58 * 1.0 / (120.0 * 30.0 / 1000.0), abs=0.01)


def test_13f_an_energy_store_without_a_soc_says_so() -> None:
    """Unknown SoC ⇒ `None`: the EV without a SoC sensor is not pretended (§5.7)."""
    store = EnergyStore(capacity_kwh=64.0, min_soc=20.0, max_soc=80.0, max_charge_w=7360.0)
    assert store.required_kwh(None, 80.0, None, ctx()) is None
    assert store.required_kwh(40.0, 80.0, None, ctx()) == pytest.approx(
        64.0 * 0.40 / 0.90, abs=0.01
    )
    assert store.capacity_kwh_per_unit() == pytest.approx(0.64)


@pytest.mark.inv("INV-56")
def test_13f2_an_energy_store_stops_at_its_ceiling() -> None:
    """A SoC ceiling is a maximum like any other (INV-56)."""
    store = EnergyStore(capacity_kwh=64.0, min_soc=20.0, max_soc=80.0, max_charge_w=7360.0)
    assert store.max_level() == pytest.approx(80.0)
    assert store.required_kwh(40.0, 100.0, None, ctx()) == pytest.approx(
        store.required_kwh(40.0, 80.0, None, ctx())
    )


def test_13f3_a_battery_can_say_what_it_could_give_back() -> None:
    """V2H and the home battery read the same store from the other side (§5.7)."""
    store = EnergyStore(
        capacity_kwh=64.0,
        min_soc=20.0,
        max_soc=80.0,
        max_charge_w=7360.0,
        reserve_soc=30.0,
        max_discharge_w=5000.0,
    )
    assert store.min_level() == pytest.approx(20.0)
    assert store.coast_hours(60.0, 30.0, ctx()) is None, "a parked battery does not leak"
    assert store.deliverable_kwh(None) is None
    assert store.deliverable_kwh(60.0) == pytest.approx((60.0 - 30.0) * 0.64 * 0.95, abs=0.01)


def test_13g_every_store_reports_its_bounds_and_its_coast() -> None:
    """The four methods D5 and D6 ask for, on each model (D4 §4.3)."""
    room = RoomStore.from_volume(volume_m3=250.0, max_c=24.0, min_c=17.0, heat_loss_w_per_k=120.0)
    assert (room.max_level(), room.min_level()) == (24.0, 17.0)
    cold = ctx(outdoor_c=-8.0)
    coast = room.coast_hours(22.0, 21.0, cold)
    assert coast is not None
    assert coast == pytest.approx(7.5 / (120.0 * 30.0 / 1000.0), abs=0.01)
    assert room.coast_hours(22.0, 21.0, ctx()) is None, "no outdoor reading, no coast"
    assert room.required_kwh(None, 22.0, None, ctx()) is None

    tank = TankStore(litres=300.0, standby_loss_w=60.0, max_c=80.0, min_c=45.0)
    assert (tank.max_level(), tank.min_level()) == (80.0, 45.0)
    tank_coast = tank.coast_hours(75.0, 45.0, ctx())
    assert tank_coast is not None
    assert tank_coast == pytest.approx(10.68 / 0.06, abs=1.0)
    assert tank.required_kwh(None, 75.0, None, ctx()) is None
    assert (
        TankStore(litres=300.0, standby_loss_w=0.0, max_c=80.0, min_c=45.0).coast_hours(
            75.0, 45.0, ctx()
        )
        is None
    )

    slab = SlabStore(area_m2=57.5, screed_mm=50.0, loss_coeff_w_per_k=120.0, max_c=27.0)
    assert slab.min_level() == pytest.approx(5.0)
    assert slab.required_kwh(None, 22.0, None, ctx()) is None
    assert slab.coast_hours(22.0, 21.0, ctx(outdoor_c=22.0)) is None, "no gradient, no loss"


def test_13h_a_cop_curve_interpolates_and_holds_its_ends() -> None:
    """The A2A curve measured in the reference house (D4 §6.4)."""
    curve = DEFAULT_COP_CURVES["a2a"]
    assert curve.at(0.0) == pytest.approx(3.0)
    assert curve.at(3.5) == pytest.approx(3.4, abs=0.01)
    assert curve.at(-40.0) == pytest.approx(1.8), "below the first anchor the curve is flat"
    assert curve.at(40.0) == pytest.approx(4.5), "and flat above the last"
    assert CopCurve.flat(1.0).at(-15.0) == pytest.approx(1.0), "a resistive loop is η = 1"
