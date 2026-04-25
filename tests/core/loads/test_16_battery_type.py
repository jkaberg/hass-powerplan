"""The battery type (D4 §6.6): signed power, a reserve floor, a 0 W fail-safe.

D4 §9 has no numbered item for the battery - its strategies are phase 5's - but
the type ships with the others, so its three load-bearing facts are pinned here:
the demand is **signed** (discharge is a negative watt figure bounded by the
inverter), the reserve is a floor the demand never asks to cross, and the kind's
release value is 0 W so an inverter left alone neither drains nor overcharges
(INV-64).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.powerplan.core.loads import LoadState, Role
from custom_components.powerplan.core.loads.kinds.modulate import Modulate
from custom_components.powerplan.core.loads.types.battery import CHEMISTRIES
from tests.core.loads.conftest import OSLO, load_ctx, load_from, materialised, reads

NOW = datetime(2026, 1, 14, 17, 41, 9, tzinfo=OSLO)
ANSWERS = {"capacity_kwh": 10.0, "max_charge_kw": 5.0, "max_discharge_kw": 5.0, "reserve_pct": 20.0}


def battery(**answers: object) -> object:
    """Return a 10 kWh LFP battery behind a 5 kW inverter."""
    return load_from("battery", {**ANSWERS, **answers})


def ctx_at(soc: float | None) -> object:
    """Return a tick with the inverter reporting `soc` percent."""
    numbers = {} if soc is None else {Role.SOC: soc}
    return load_ctx(now=NOW, reads=reads(NOW, numbers=numbers), zone=OSLO)


def test_the_derivation_takes_the_usable_window_from_the_chemistry() -> None:
    """LFP 95 %, NMC 90 % usable; the reserve becomes the store's floor (D4 §6.6)."""
    lfp = materialised("battery", ANSWERS)["params"]
    nmc = materialised("battery", {**ANSWERS, "chemistry": "nmc"})["params"]

    assert lfp["usable_kwh"] == pytest.approx(10.0 * CHEMISTRIES["lfp"].usable_fraction)
    assert nmc["usable_kwh"] == pytest.approx(10.0 * CHEMISTRIES["nmc"].usable_fraction)
    assert lfp["reserve_soc"] == 20.0
    assert lfp["nameplate_w"] == 5000.0
    assert lfp["max_discharge_w"] == 5000.0


def test_the_demand_is_signed_between_the_inverter_limits() -> None:
    """At 50 % the battery may charge to +5 kW and discharge to −5 kW (signed power)."""
    load = battery()
    demand = load.device_type.demand(load, LoadState(), ctx_at(50.0))  # type: ignore[attr-defined]

    assert demand.wants
    assert demand.max_w == 5000.0
    assert demand.min_w == -5000.0
    assert demand.required_kwh is not None
    assert demand.required_kwh > 0.0


def test_the_reserve_is_a_floor_the_demand_never_crosses() -> None:
    """At the reserve there is nothing to discharge; below it the floor is violated."""
    load = battery()
    at_reserve = load.device_type.demand(load, LoadState(), ctx_at(20.5))  # type: ignore[attr-defined]
    below = load.device_type.demand(load, LoadState(), ctx_at(12.0))  # type: ignore[attr-defined]

    assert at_reserve.min_w == 0.0, "no discharge offered at the reserve"
    assert at_reserve.max_w == 5000.0, "charging is still on the table"
    assert below.comfort is not None
    assert below.comfort.violated
    assert below.min_w == 0.0


def test_a_full_battery_asks_for_nothing_and_offers_discharge() -> None:
    """At the ceiling only the discharge side remains."""
    load = battery()
    demand = load.device_type.demand(load, LoadState(), ctx_at(100.0))  # type: ignore[attr-defined]

    assert demand.max_w == 0.0
    assert demand.min_w == -5000.0
    assert demand.required_kwh == 0.0


def test_grid_charging_off_leaves_only_the_discharge_side() -> None:
    """`allow_grid_charge = no`: the allocator may not charge it from the grid (D-0209)."""
    load = battery(allow_grid_charge=False)
    demand = load.device_type.demand(load, LoadState(), ctx_at(50.0))  # type: ignore[attr-defined]

    assert demand.max_w == 0.0
    assert demand.min_w == -5000.0


@pytest.mark.inv("INV-64")
def test_the_release_value_is_zero_watts() -> None:
    """A released inverter is parked at 0 W: livable without powerplan (INV-64)."""
    load = battery()
    kind = load.kind  # type: ignore[attr-defined]

    assert isinstance(kind, Modulate)
    assert kind.cfg.signed
    assert kind.cfg.release_value == 0.0
    assert kind.cfg.enable_role is None
    assert kind.cfg.role is Role.BATTERY_POWER_SET
