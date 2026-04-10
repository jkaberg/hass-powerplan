"""D4 §9 4 - `MODULATE`: rounding, the 6 A cliff, the ramp, suppression (INV-28).

Three nights of the ancestor controller are in this file:

* **The dropped sessions** - twelve and 0.2–3.4 kWh delivered in hours
  where 6.5 kWh was available, because a positive grant below 6 A paused the
  charger. IEC 61851's pilot duty cycle bottoms out at 6 A; there is no duty
  cycle that means 4 A, so below the floor the car opens its contactor and stops
  looking for ten minutes (INV-28).
* **"A vetoed stop is a hold"**: for a day a vetoed stop meant
  "hold at the minimum start current", which charged the car at 1.3 kW through
  every slot it was not scheduled for, and on a disabled charger meant
  *enabling* it - five hours of HH:50 on, HH:00 off.
* **The square wave** - every 30 seconds, 22/16/23/16/23/16/23. The way
  down may be a step; the way up is a ramp of `step_up_a` per write.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import Action, Hold, Role
from tests.core.loads.conftest import (
    NOW,
    W_PER_AMP,
    FakeCharger,
    ev_load,
    grant,
    kind_ctx,
    load_ctx,
    load_state,
    modulate_kind,
    reads,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.loads import KindCtx


def ctx_for(charger: FakeCharger, **kwargs: Any) -> KindCtx:
    """Return the kind context a charger presents this tick."""
    return kind_ctx(
        reads=charger.reads_at(),
        held=charger.limit_a,
        session_active=charger.status == "charging",
        enabled=charger.enabled,
        max_value=32.0,
        **kwargs,
    )


@pytest.mark.inv("INV-28")
@pytest.mark.parametrize(
    ("granted_w", "amps"),
    [
        (15.9 * W_PER_AMP, 15.0),
        (15.999 * W_PER_AMP, 15.0),
        (32.0 * W_PER_AMP, 32.0),
        (6.0 * W_PER_AMP, 6.0),
        (40.0 * W_PER_AMP, 32.0),
    ],
)
def test_04_rounding_is_down_always(granted_w: float, amps: float, charger: FakeCharger) -> None:
    """15.9 A is written as 15; 32 A never becomes 31 (`AMP_EPS`); 40 A is capped."""
    quantised = modulate_kind().quantise(granted_w, ctx_for(charger))
    assert quantised.value == pytest.approx(amps)
    assert quantised.effective_w == pytest.approx(amps * W_PER_AMP)


@pytest.mark.inv("INV-25")
@pytest.mark.inv("INV-28")
def test_04b_below_the_cliff_clamps_to_six_while_a_session_runs(charger: FakeCharger) -> None:
    """A 0 W grant at 16 A goes to 6 A and keeps the session - never holds 16 A."""
    kind = modulate_kind()
    quantised = kind.quantise(0.0, ctx_for(charger))
    assert quantised.value == pytest.approx(6.0)
    assert quantised.floored
    assert not quantised.stop
    assert quantised.effective_w == pytest.approx(6.0 * W_PER_AMP), (
        "the allocator is charged the floor it will actually draw, not the grant"
    )

    command = kind.command(quantised, grant(w=0.0), ctx_for(charger))
    assert not isinstance(command, Hold)
    assert command.value == pytest.approx(6.0)
    assert not command.sheds, "a zero grant is not a shed (INV-25)"
    charger.step(30.0, command)
    assert charger.status == "charging", "the session survived"


@pytest.mark.inv("INV-28")
def test_04c_a_stop_the_allocator_authorised_parks_the_charger(charger: FakeCharger) -> None:
    """A stop switches off and parks the limit at 0 A: a stopped charger says so."""
    kind = modulate_kind()
    quantised = kind.quantise(0.0, ctx_for(charger, stop_ok=True))
    assert quantised.stop
    assert quantised.effective_w == 0.0

    command = kind.command(quantised, grant(w=0.0, stop_ok=True, shed=True), ctx_for(charger))
    assert not isinstance(command, Hold)
    assert [write.role for write in command.writes] == [Role.ENABLE, Role.CURRENT_SET]
    assert [write.value for write in command.writes] == [False, 0.0]
    charger.step(30.0, command)
    assert not charger.enabled
    assert charger.limit_a == 0.0


@pytest.mark.inv("INV-25")
@pytest.mark.inv("INV-28")
def test_04d_a_vetoed_stop_on_a_stopped_charger_is_a_hold(charger: FakeCharger) -> None:
    """No write, no enable, no re-arm: the 6 A floor belongs to a car being trimmed."""
    charger.enabled = False
    charger.limit_a = 0.0
    charger.status = "awaiting_start"

    kind = modulate_kind()
    quantised = kind.quantise(0.0, ctx_for(charger))
    assert quantised.hold
    outcome = kind.command(quantised, grant(w=0.0), ctx_for(charger))
    assert isinstance(outcome, Hold)
    assert outcome.action is Action.SAME
    assert charger.writes == []


@pytest.mark.inv("INV-28")
def test_04e_the_way_back_up_is_a_ramp(charger: FakeCharger) -> None:
    """6 → 28 A takes at least five writes; one tick of phantom headroom cannot."""
    charger.limit_a = 6.0
    kind = modulate_kind(step_up=4.0)
    writes = 0
    for tick in range(12):
        ctx = ctx_for(charger)
        quantised = kind.quantise(28.0 * W_PER_AMP, ctx)
        command = kind.command(quantised, grant(w=28.0 * W_PER_AMP), ctx)
        if isinstance(command, Hold):
            break
        writes += 1
        charger.step(60.0, command, at=NOW + timedelta(minutes=tick))
        assert charger.limit_a <= 6.0 + 4.0 * writes
        if charger.limit_a >= 28.0:
            break
    assert writes >= 5, f"reached {charger.limit_a} A in {writes} writes"
    assert charger.limit_a == pytest.approx(28.0)


@pytest.mark.inv("INV-28")
def test_04f_write_suppression_needs_two_amps_or_a_stale_value(charger: FakeCharger) -> None:
    """461 writes in 24 hours was ±1 A chasing a residual that moves with every kettle."""
    kind = modulate_kind(suppress_delta=2.0, suppress_stale_s=60)
    charger.limit_a = 16.0

    one_amp_up = kind.command(
        kind.quantise(17.0 * W_PER_AMP, ctx_for(charger)),
        grant(w=17.0 * W_PER_AMP),
        ctx_for(charger),
    )
    assert isinstance(one_amp_up, Hold)
    assert one_amp_up.action is Action.HELD_SUPPRESSED

    two_amps_up = kind.command(
        kind.quantise(18.0 * W_PER_AMP, ctx_for(charger)),
        grant(w=18.0 * W_PER_AMP),
        ctx_for(charger),
    )
    assert not isinstance(two_amps_up, Hold)

    stale = kind_ctx(
        reads=charger.reads_at(age_s=90.0),
        held=16.0,
        session_active=True,
        enabled=True,
        max_value=32.0,
    )
    aged = kind.command(kind.quantise(17.0 * W_PER_AMP, stale), grant(w=17.0 * W_PER_AMP), stale)
    assert not isinstance(aged, Hold), "≥ 60 s stale and different at all is worth a write"


@pytest.mark.inv("INV-28")
def test_04g_shedding_is_exempt_from_suppression(charger: FakeCharger) -> None:
    """A limit coming down is what keeps the ceiling and goes out at once."""
    kind = modulate_kind(suppress_delta=2.0)
    charger.limit_a = 16.0
    down = kind.command(
        kind.quantise(15.0 * W_PER_AMP, ctx_for(charger)),
        grant(w=15.0 * W_PER_AMP, shed=True, shed_reason="stage 1", stage=1),
        ctx_for(charger),
    )
    assert not isinstance(down, Hold)
    assert down.value == pytest.approx(15.0)
    assert down.urgent, "any reduction for a modulating load is urgent (§5.10)"


@pytest.mark.inv("INV-28")
def test_04h_an_unreadable_limit_constrains_nothing(charger: FakeCharger) -> None:
    """`unknown` arriving as 0 A must never clamp the charger to a standstill."""
    without_max = reads(
        numbers={Role.CURRENT_SET: 16.0, Role.POWER: charger.measured_w},
        texts={Role.STATUS: "charging", Role.ENABLE: "on"},
    )
    ctx = kind_ctx(reads=without_max, held=16.0, session_active=True, enabled=True, max_value=None)
    quantised = modulate_kind().quantise(32.0 * W_PER_AMP, ctx)
    assert quantised.value == pytest.approx(32.0)


@pytest.mark.inv("INV-25")
def test_04i_a_zero_grant_that_is_not_a_shed_leaves_no_shed_latch(charger: FakeCharger) -> None:
    """A load that simply does not want power is never recorded as shed (INV-25)."""
    load = ev_load()
    state, result = load.apply(
        grant(w=0.0),
        load_state(),
        load_ctx(reads=charger.reads_at()),
    )
    assert not state.shed_active
    assert result.effective_w == pytest.approx(6.0 * W_PER_AMP)
