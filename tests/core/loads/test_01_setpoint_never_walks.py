"""D4 §9 1 - ten starts against a thermostat that keeps its value (INV-27, INV-29).

The defect this file exists to catch, from the reference house: once,
eight pyscript reloads in six minutes walked one heat pump 22 → 23 → 24 → 25 →
26 °C, because the driver took its baseline from the thermostat and then added
its own offset on top. What it "remembered" was its own previous write, so
start-up was not idempotent and the error compounded. The room reached 29 °C
with 14–15 °C outdoors and the controller read its own output as rising demand.

So: the comfort value comes from configuration (INV-27), start-up **restores**
it rather than adopting what it finds, and no upward move happens within one
dwell of a restore (INV-29).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads import Action, Role
from tests.core.loads.conftest import (
    NOW,
    FakeThermostat,
    floor_load,
    grant,
    load_ctx,
    load_state,
)


@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-29")
def test_01_ten_starts_in_a_row_never_walk_the_setpoint(thermostat: FakeThermostat) -> None:
    """Ten cold starts: the setpoint lands on the configured comfort and stays."""
    load = floor_load()
    for start in range(10):
        at = NOW + timedelta(minutes=30 * start)
        state = load_state()  # a cold start with nothing restored from the store
        ctx = load_ctx(now=at, reads=thermostat.reads_at(at))
        state, result = load.restore(state, ctx, reason="startup")
        thermostat.step(1.0, result.command, at=at)
        assert thermostat.setpoint == pytest.approx(24.0), f"start {start}"

    assert thermostat.writes == [(Role.SETPOINT, 24.0)], (
        "the first start corrects 22 → 24; the nine after it find 24 and say nothing"
    )


@pytest.mark.inv("INV-27")
def test_01b_a_restart_after_a_shed_restores_comfort_not_comfort_plus_a_band(
    thermostat: FakeThermostat,
) -> None:
    """A shed lowers the loop; the restart puts it back at 24 °C, never at 25."""
    load = floor_load()
    state = load_state()

    shed = grant(w=0.0, shed=True, shed_reason="stage 2", stage=2)
    ctx = load_ctx(reads=thermostat.reads_at(NOW))
    state, result = load.apply(shed, state, ctx)
    assert result.action is Action.WRITTEN
    thermostat.step(60.0, result.command, at=NOW)
    # The floor is 21 °C and the swing 1 K: the lowest setpoint that keeps the
    # floor is 21.5 (D4 §5.4, `design/DECISIONS.md` D-0259).
    assert thermostat.setpoint == pytest.approx(21.5)
    assert state.shed_active

    later = NOW + timedelta(minutes=20)
    fresh = load_state()  # HA restarted; the store had nothing
    fresh, restored = load.restore(fresh, load_ctx(now=later, reads=thermostat.reads_at(later)))
    thermostat.step(1.0, restored.command, at=later)
    assert thermostat.setpoint == pytest.approx(24.0)
    assert fresh.last_target_restore_at == later


@pytest.mark.inv("INV-29")
def test_01c_no_upward_move_within_one_dwell_of_a_restore(thermostat: FakeThermostat) -> None:
    """Without this the next tick re-adds the offset and the value oscillates."""
    load = floor_load()
    state = load_state()
    state, restored = load.restore(state, load_ctx(reads=thermostat.reads_at(NOW)))
    thermostat.step(1.0, restored.command, at=NOW)

    # A cheap slot wants +1 K. Inside the restore dwell it waits.
    soon = NOW + timedelta(minutes=5)
    _, held = load.apply(
        grant(w=960.0),
        state,
        load_ctx(now=soon, reads=thermostat.reads_at(soon), setpoint_delta=1.0),
    )
    assert held.action is Action.HELD_DWELL
    assert held.command is None

    # Lowering is never blocked: shedding must survive a restart. A slab's shed
    # is urgent from stage 3, where a thermostat on a tank is urgent from
    # stage 2 (§5.10), so this is the stage that must get out inside the clocks.
    _, shed = load.apply(
        grant(w=0.0, shed=True, shed_reason="stage 3", stage=3),
        state,
        load_ctx(now=soon, reads=thermostat.reads_at(soon)),
    )
    assert shed.action is Action.WRITTEN
    assert shed.command is not None
    assert shed.command.value == pytest.approx(21.5), "the floor plus half the swing (D-0259)"
