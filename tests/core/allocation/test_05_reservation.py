"""D6 §9 5 - what a grant actually costs the headroom (D6 §5.2).

**On the ancestor controller.** The planner paced the tank in fractional watts and the
allocator subtracted *that* from the free power: `granted_w 348.3, measured_w 2940.0`,
and `p_free_w` read 8–9 kW while the house was 1.4 kW over its allowance. An on/off
element is a relay, not a dimmer: it draws its nameplate or nothing, and what it
reserves is the nameplate whatever the plan paced it at.

The mirror-image mistake is a thermostatic inverter: reserving `rated_w` for a
heat pump modulating at 23 W eats 3 kW of headroom and starves everything else,
and the published table then invites exactly the wrong conclusion (the pump is
*saturated*, not starved). It reserves what it measures plus room to modulate up.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.allocation import (
    GRANT_MARGIN_W,
    ON_W,
    AllocCfg,
    AllocState,
    allocate,
    reserved_w,
)
from custom_components.powerplan.core.model import Mode
from tests.core.allocation.conftest import (
    W_PER_AMP,
    alloc_ctx,
    budget_of,
    controlled,
    ev_view,
    loop_view,
    meter,
    pump_view,
    tank_view,
)


def test_05_a_paced_relay_reserves_its_nameplate_not_its_grant() -> None:
    """The ancestor controller's frame: granted 348.3 W, measured 2 940 W, reserved 3 000 W."""
    tank = tank_view()

    reserved = reserved_w(tank, 348.3, controlled("tank", measured_w=2940.0))

    assert reserved == pytest.approx(3000.0)


def test_05_a_relay_already_on_reserves_its_nameplate_on_a_zero_grant() -> None:
    """A closed relay draws its element whatever this tick decided (§5.2)."""
    tank = tank_view()

    assert reserved_w(tank, 0.0, controlled("tank", measured_w=2940.0)) == pytest.approx(3000.0)
    assert reserved_w(tank, 0.0, controlled("tank", measured_w=0.0)) == 0.0
    assert ON_W == 50.0


def test_05_a_thermostatic_inverter_reserves_measured_plus_the_margin() -> None:
    """23 W measured reserves 523 W, not the 3 000 W of rated power."""
    pump = pump_view()

    reserved = reserved_w(pump, 3000.0, controlled("pump", measured_w=23.0))

    assert GRANT_MARGIN_W == 500.0
    assert reserved == pytest.approx(523.0)
    assert reserved < pump.nameplate_w


def test_05_a_thermostatic_reservation_is_capped_at_rated_power() -> None:
    """Measured 2 900 W + 500 W of margin is still only 3 000 W of pump (§5.2)."""
    pump = pump_view()

    assert reserved_w(pump, 3000.0, controlled("pump", measured_w=2900.0)) == pytest.approx(3000.0)


def test_05_a_thermostatic_load_with_nothing_measured_reserves_its_ceiling() -> None:
    """Unmeasured is not "zero": the forecast ceiling is rated power (D4 §5.4)."""
    pump = pump_view()

    assert reserved_w(pump, 3000.0, controlled("pump", measured_w=None)) == pytest.approx(3000.0)


def test_05_a_modulating_charger_reserves_what_it_was_told() -> None:
    """A continuously variable grant is a real figure and stands as it is."""
    ev = ev_view()

    assert reserved_w(ev, 20.0 * W_PER_AMP, controlled("ev", measured_w=0.0)) == pytest.approx(4600)
    assert reserved_w(ev, 0.0, controlled("ev", measured_w=7000.0)) == 0.0


def test_05_a_delegated_load_reserves_its_nameplate_whatever_we_granted() -> None:
    """Someone else drives it; powerplan only keeps its nameplate clear (D4 §5.2)."""
    ev = ev_view(mode=Mode.DELEGATED)

    assert reserved_w(ev, 0.0, controlled("ev", measured_w=0.0)) == pytest.approx(ev.nameplate_w)


def test_05_a_settling_write_reserves_what_was_commanded_not_the_lagging_sensor() -> None:
    """INV-18: for one poll after a write the sensor still shows the old current.

    That antiphase error is the 30-second square wave; the
    reservation reads the command while the write settles.
    """
    loop = loop_view()

    reserved = reserved_w(
        loop, 0.0, controlled("loop_bath", measured_w=0.0, commanded_w=960.0, settling=True)
    )

    assert reserved == pytest.approx(960.0)


def test_05_a_load_with_no_meter_row_at_all_reserves_on_its_grant_alone() -> None:
    """Nothing measured and nothing commanded: the grant is all there is."""
    tank = tank_view()

    assert reserved_w(tank, 3000.0, None) == pytest.approx(3000.0)
    assert reserved_w(tank, 0.0, None) == 0.0


def test_05_the_uncontrolled_baseline_is_subtracted_before_any_load_asks() -> None:
    """The allowance covers the whole site: the oven is not the tank's headroom.

    σ covers the *deviation* of the uncontrolled load, never its level (§5.1), so
    the level comes off the allowance before the walk starts.
    """
    tank = tank_view()
    ctx = alloc_ctx([tank], budget=budget_of(3500.0), meter_snapshot=meter(uncontrolled_w=1000.0))

    grants, report, _state = allocate(ctx, (), AllocCfg(), AllocState())

    assert grants["tank"].w == 0.0
    assert report.p_free_w == pytest.approx(2500.0)
