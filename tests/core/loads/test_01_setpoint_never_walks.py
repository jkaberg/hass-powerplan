"""D4 §9 1 - ten starts against a thermostat that keeps its value (INV-27, INV-29).

The defect this file exists to catch, from the ancestor controller: eight
pyscript reloads in six minutes walked one heat pump 22 → 23 → 24 → 25 →
26 °C, because the driver took its baseline from the thermostat and then added
its own offset on top. What it "remembered" was its own previous write, so
start-up was not idempotent and the error compounded. The room reached 29 °C
with 14–15 °C outdoors and the controller read its own output as rising demand.

So: the comfort value comes from configuration (INV-27), start-up **restores**
it rather than adopting what it finds, and no upward move happens within one
dwell of a restore (INV-29).

A restore undoes only a write powerplan itself made and has on record, and puts
the device back to what it held before that write - the record, persisted with
the load, is never powerplan's own value, which is what keeps a start from
walking. A thermostat powerplan never wrote to keeps what it holds, however
many times Home Assistant starts; the first tick in control is then what steers
it, and in observe nothing does.
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
    """Ten cold starts against a thermostat powerplan never wrote to: nothing moves it."""
    load = floor_load()
    for start in range(10):
        at = NOW + timedelta(minutes=30 * start)
        state = load_state()  # a cold start with nothing restored from the store
        ctx = load_ctx(now=at, reads=thermostat.reads_at(at))
        state, result = load.restore(state, ctx, reason="startup")
        assert result.action is Action.SAME, f"start {start}"
        assert result.command is None
        thermostat.step(1.0, result.command, at=at)
        assert thermostat.setpoint == pytest.approx(22.0), f"start {start}"

    assert thermostat.writes == [], "nothing of ours is on record, so nothing is restored"


@pytest.mark.inv("INV-27")
@pytest.mark.inv("INV-29")
def test_01a_ten_starts_after_our_own_write_restore_once_and_never_walk(
    thermostat: FakeThermostat,
) -> None:
    """Ten starts, each from the store: our coast is undone once, then nothing."""
    load = floor_load()
    # A previous run coasted the loop a kelvin under its comfort; the store kept it.
    state, coast = load.apply(
        grant(w=960.0), load_state(), load_ctx(reads=thermostat.reads_at(), setpoint_delta=-1.0)
    )
    assert coast.action is Action.WRITTEN
    assert coast.value == pytest.approx(23.0)
    assert state.prior == {"setpoint": 22.0}, "what the thermostat held before our write"
    thermostat.step(1.0, coast.command)
    thermostat.writes.clear()

    for start in range(10):
        at = NOW + timedelta(minutes=30 * (start + 1))
        ctx = load_ctx(now=at, reads=thermostat.reads_at(at))
        state, result = load.restore(state, ctx, reason="startup")
        thermostat.step(1.0, result.command, at=at)
        assert thermostat.setpoint == pytest.approx(22.0), f"start {start}"

    assert thermostat.writes == [(Role.SETPOINT, 22.0)], (
        "the first start undoes our 23 → 22; the nine after it find nothing of ours"
    )


@pytest.mark.inv("INV-27")
def test_01b_a_restart_after_a_shed_undoes_it_and_never_adds_a_band(
    thermostat: FakeThermostat,
) -> None:
    """A shed lowers the loop; the restart puts it back where it was, 22 °C - never 25."""
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
    # HA restarted without letting go (a crash): the store kept the shed's record.
    kept, restored = load.restore(state, load_ctx(now=later, reads=thermostat.reads_at(later)))
    assert restored.action is Action.WRITTEN
    thermostat.step(1.0, restored.command, at=later)
    assert thermostat.setpoint == pytest.approx(22.0), "what it held before the shed"
    assert kept.last_target_restore_at == later
    assert kept.prior == {}, "undone: nothing of ours left on record"

    # The same restart with nothing in the store: the shed is not ours on record,
    # so it stays where it is - the first tick in control is the correction.
    thermostat.setpoint = 21.5
    fresh, untouched = load.restore(
        load_state(), load_ctx(now=later, reads=thermostat.reads_at(later))
    )
    assert untouched.action is Action.SAME
    assert untouched.command is None
    assert fresh.last_target_restore_at is None


@pytest.mark.inv("INV-29")
def test_01c_no_upward_move_within_one_dwell_of_a_restore(thermostat: FakeThermostat) -> None:
    """Without this the next tick re-adds the offset and the value oscillates."""
    load = floor_load()
    # A coast of ours on record, so the start has something to restore.
    earlier = NOW - timedelta(hours=1)
    state, coast = load.apply(
        grant(w=960.0),
        load_state(),
        load_ctx(now=earlier, reads=thermostat.reads_at(earlier), setpoint_delta=-1.0),
    )
    thermostat.step(1.0, coast.command, at=earlier)
    state, restored = load.restore(state, load_ctx(reads=thermostat.reads_at(NOW)))
    assert restored.action is Action.WRITTEN
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
