"""D6 §9 31 - an idle on/off thermostat reserves its plan's draw, not a margin (D-0686).

The reference house on 26 Sep 2026 at a 4.7 kWh target: five idle floors and the
tank each held 500 W, Σ reserved 4 424 W against a 4 964 W allowance, and the
entrance floor was refused at every hour's start. A relay behind a thermostat draws
its nameplate or nothing; idle, it takes what its plan says for the slot - a banked
floor's standing loss - and a relay that closes is at its nameplate on the next tick.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import GRANT_MARGIN_W, reserved_w
from custom_components.powerplan.core.strategies import LoadView
from tests.core.allocation.conftest import controlled, demand, loop_view, pump_view
from tests.core.loads.conftest import floor_load, load_from


def _floor(load_id: str, *, planned_w: float = 0.0):
    """Return a `mode` floor as the tick builds it: a relay behind a thermostat (D5 §4)."""
    return loop_view(
        load_id=load_id,
        kind="mode",
        thermostatic=True,
        relay_thermostat=True,
        planned_draw_w=planned_w,
    )


def test_31_five_idle_floors_reserve_their_plans_draw_not_five_margins() -> None:
    """Σ planned standing loss, not 5 × 500 W."""
    floors = [_floor(f"floor_{i}", planned_w=40.0 * (i + 1)) for i in range(5)]

    total = sum(
        reserved_w(floor, 0.0, controlled(floor.load_id, measured_w=0.05)) for floor in floors
    )

    assert total == pytest.approx(40.0 + 80.0 + 120.0 + 160.0 + 200.0)
    assert total < 5 * GRANT_MARGIN_W


def test_31_an_idle_thermostat_with_no_plan_reserves_nothing() -> None:
    """No plan, no standing loss to hold back: 0, and the next tick catches a relay that closes."""
    floor = _floor("floor")

    assert reserved_w(floor, 0.0, controlled("floor", measured_w=0.0)) == 0.0


def test_31_a_relay_that_closes_or_is_granted_reserves_its_nameplate() -> None:
    """On or granted, the relay draws its element (D-0169); unmeasured, it may be on."""
    floor = _floor("floor", planned_w=40.0)

    assert reserved_w(floor, 0.0, controlled("floor", measured_w=950.0)) == pytest.approx(960.0)
    assert reserved_w(floor, 960.0, controlled("floor", measured_w=0.0)) == pytest.approx(960.0)
    assert reserved_w(floor, 0.0, None) == pytest.approx(960.0)


def test_31_a_heat_pump_still_reserves_measured_plus_the_margin() -> None:
    """The modulating kind keeps D-0168's rule: 23 W measured reserves 523 W (test 5)."""
    pump = pump_view()

    assert reserved_w(pump, 0.0, controlled("pump", measured_w=23.0)) == pytest.approx(523.0)


def test_31_the_tick_builds_floors_and_tanks_as_relays_and_a_heat_pump_as_neither() -> None:
    """`LoadView.of` marks a `mode`/`setpoint` load a relay thermostat unless it is a heat pump."""
    floor = LoadView.of(floor_load(), demand(max_w=960.0))
    tank = LoadView.of(load_from("water_heater", {}, load_id="tank"), demand(max_w=3000.0))
    pump = LoadView.of(
        load_from(
            "heat_pump",
            {"hp_type": "a2a", "rated_kw": 1.5, "area_m2": 60.0, "building": "2000_2010"},
            load_id="pump",
        ),
        demand(max_w=1500.0),
    )

    assert floor.relay_thermostat
    assert tank.relay_thermostat
    assert not pump.relay_thermostat
    assert pump.thermostatic
