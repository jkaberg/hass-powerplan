"""D4 §9 7 and 8 - the WriteGate decision matrix and the transport budget.

Every row of D4 §5.10, table-driven, against the **pure** decision
(`core/loads/gate.py`, PLAN §7 dec. 5). The executor half - `blocking=True` on
the service call itself, the verify read-back being scheduled on the HA clock -
is WP2.1; what is asserted here is that the decision the executor carries out
says `blocking` and carries a `verify_at` (INV-24), and that no row can be
bought past by the load mode `force` (INV-21).

Item 8 (the transport budget, INV-58) is row 8 of the same matrix, so it is
tested in this file rather than in one of its own.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.powerplan.core.loads import Mode, Role
from custom_components.powerplan.core.loads.gate import (
    TRANSIENT_GRACE_S,
    UNHEALTHY_AT,
    Action,
    Command,
    Transport,
    Write,
    decide,
    failed,
    same,
    succeeded,
    transient,
    verify,
)
from tests.core.loads.conftest import NOW, budget, gate_config, gate_state

SETPOINT_22 = Command(writes=(Write(Role.SETPOINT, 22.0),), reason="plan", want_on=True)
SETPOINT_19 = Command(
    writes=(Write(Role.SETPOINT, 19.0),), reason="shed stage 2", want_on=False, sheds=True
)
SHED_URGENT = Command(
    writes=(Write(Role.SETPOINT, 19.0),),
    reason="shed stage 2",
    urgent=True,
    want_on=False,
    sheds=True,
)
BLUNT_SHED = Command(
    writes=(Write(Role.SETPOINT, 19.0),),
    reason="main fuse",
    urgent=True,
    blunt=True,
    want_on=False,
    sheds=True,
)


@pytest.mark.inv("INV-20")
def test_07a_row_1_observe_never_writes() -> None:
    """Row 1: a load in `observe` logs the would-be write and writes nothing."""
    decision = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.OBSERVE,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert decision.action is Action.OBSERVE
    assert decision.command is None
    assert decision.value == 22.0
    assert decision.gate == gate_state(observed=22.0), "no clock moved; the would-be value noted"


@pytest.mark.inv("INV-21")
@pytest.mark.inv("INV-22")
def test_07a2_observe_decides_against_what_the_device_holds() -> None:
    """Row 1 after row 3: a device already at the would-be value has no would-be write (F-4).

    The house logged "would write 21.0" to a heat pump at 21.0 on every tick. An
    observed decision carries the device's value, so its line reads old → new.
    """
    held = decide(
        SETPOINT_22,
        current=22.0,
        mode=Mode.OBSERVE,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert held.action is Action.SAME
    assert held.command is None

    moved = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.OBSERVE,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert moved.action is Action.OBSERVE
    assert moved.current == 21.0, "the old of the line's old → new"


@pytest.mark.inv("INV-20")
@pytest.mark.parametrize(
    ("mode", "action"),
    [(Mode.DELEGATED, Action.DELEGATED), (Mode.OFF, Action.SAME)],
)
def test_07b_row_2_delegated_and_off_never_write(mode: Mode, action: Action) -> None:
    """Row 2: `delegated` and `off` never write - `off` writes only via `release()`."""
    decision = decide(
        SETPOINT_22,
        current=21.0,
        mode=mode,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert decision.action is action
    assert decision.command is None


@pytest.mark.inv("INV-21")
@pytest.mark.parametrize("command", [SETPOINT_22, SHED_URGENT, BLUNT_SHED])
@pytest.mark.parametrize("mode", [Mode.AUTO, Mode.FORCE])
def test_07c_row_3_same_wins_over_urgent_blunt_and_force(command: Command, mode: Mode) -> None:
    """Row 3: a value the device already holds is never sent - whatever else is true."""
    decision = decide(
        command,
        current=float(command.value),
        mode=mode,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert decision.action is Action.SAME
    assert decision.command is None


def test_07c2_row_3_tolerance_is_the_kinds_not_a_guess() -> None:
    """Row 3 compares within the kind's tolerance: 0.04 K is the same, 0.06 K is not."""
    cfg = gate_config(tolerance=0.05)
    assert (
        decide(
            SETPOINT_22,
            current=21.96,
            mode=Mode.AUTO,
            cfg=cfg,
            state=gate_state(),
            budget=budget(),
            now=NOW,
        ).action
        is Action.SAME
    )
    assert (
        decide(
            SETPOINT_22,
            current=21.94,
            mode=Mode.AUTO,
            cfg=cfg,
            state=gate_state(),
            budget=budget(),
            now=NOW,
        ).action
        is Action.WRITTEN
    )


def test_07c3_an_unreadable_but_available_value_is_written() -> None:
    """No current value is not a reason to withhold a command we decided on (§5.10)."""
    decision = decide(
        SETPOINT_22,
        current=None,
        mode=Mode.AUTO,
        cfg=gate_config(),
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert decision.action is Action.WRITTEN


@pytest.mark.inv("INV-23")
def test_07d_row_4_unavailable_is_transient_for_the_grace_then_a_failure() -> None:
    """Row 4: 15 s of `unavailable` is a transient; longer is a failure (INV-23)."""
    cfg = gate_config()
    first = decide(
        SETPOINT_22,
        current=None,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(),
        budget=budget(),
        now=NOW,
        available=False,
    )
    assert first.action is Action.TRANSIENT
    assert first.gate.transient_since == NOW
    assert first.gate.failures == 0

    inside = decide(
        SETPOINT_22,
        current=None,
        mode=Mode.AUTO,
        cfg=cfg,
        state=first.gate,
        budget=budget(),
        now=NOW + timedelta(seconds=TRANSIENT_GRACE_S - 1),
        available=False,
    )
    assert inside.action is Action.TRANSIENT
    assert inside.gate.failures == 0

    outside = decide(
        SETPOINT_22,
        current=None,
        mode=Mode.AUTO,
        cfg=cfg,
        state=first.gate,
        budget=budget(),
        now=NOW + timedelta(seconds=TRANSIENT_GRACE_S + 1),
        available=False,
    )
    assert outside.action is Action.FAILED
    assert outside.gate.failures == 1
    assert not outside.gate.unhealthy


@pytest.mark.inv("INV-23")
def test_07d2_a_link_that_came_back_is_a_success() -> None:
    """Any success - a command that landed, a link that came back - resets the count."""
    state = gate_state(failures=1, transient_since=NOW)
    back = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=gate_config(),
        state=state,
        budget=budget(),
        now=NOW + timedelta(seconds=30),
    )
    assert back.action is Action.WRITTEN
    assert back.gate.failures == 0
    assert back.gate.transient_since is None


@pytest.mark.inv("INV-23")
def test_07d3_unhealthy_at_two_consecutive_failures() -> None:
    """`unhealthy` needs N consecutive failures and N is 2 (§5.10)."""
    state = gate_state()
    for _ in range(UNHEALTHY_AT):
        state = failed(state, "ServiceValidationError: refused", NOW)
    assert state.failures == UNHEALTHY_AT
    assert state.unhealthy
    assert succeeded(state).failures == 0
    assert not succeeded(state).unhealthy
    assert transient(gate_state(), NOW).transient_since == NOW


def test_07e_row_5_settling_holds_the_way_up_but_not_the_way_down() -> None:
    """Row 5: an upward write inside the settle window waits; a shed does not.

    The interval is zeroed so that row 5 is the only row in play - rows 6 and 7
    have their own cases below.
    """
    cfg = gate_config(verify_after_s=60.0, min_interval_s=0.0)
    settling = gate_state(
        last_write_at=NOW, last_value=21.0, verify_due=NOW + timedelta(seconds=60)
    )
    at = NOW + timedelta(seconds=10)
    assert (
        decide(
            SETPOINT_22,
            current=21.0,
            mode=Mode.AUTO,
            cfg=cfg,
            state=settling,
            budget=budget(),
            now=at,
        ).action
        is Action.HELD_SETTLING
    )
    assert (
        decide(
            SETPOINT_19,
            current=21.0,
            mode=Mode.AUTO,
            cfg=cfg,
            state=settling,
            budget=budget(),
            now=at,
        ).action
        is Action.WRITTEN
    )


def test_07e2_a_blunt_reason_overrides_the_settle_window_and_the_clocks() -> None:
    """A breaker beats a budget, and it beats every politeness clock with it.

    Rows 5, 6 and 7 all yield to a blunt reason; row 3 does not (`test_07c`).
    """
    settling = gate_state(
        last_write_at=NOW, last_value=21.0, verify_due=NOW + timedelta(seconds=60), last_on_at=NOW
    )
    blunt_up = Command(
        writes=(Write(Role.SETPOINT, 22.0),), reason="fuse", blunt=True, want_on=True
    )
    decision = decide(
        blunt_up,
        current=21.0,
        mode=Mode.AUTO,
        cfg=gate_config(min_interval_s=600.0, min_on_s=600.0, min_off_s=600.0),
        state=settling,
        budget=budget(),
        now=NOW + timedelta(seconds=10),
    )
    assert decision.action is Action.WRITTEN


def test_07f_row_6_the_command_interval_holds_unless_urgent() -> None:
    """Row 6: inside `min_interval` nothing but an urgent write gets out."""
    cfg = gate_config(min_interval_s=120.0)
    state = gate_state(last_write_at=NOW, last_value=21.0)
    at = NOW + timedelta(seconds=60)
    assert (
        decide(
            SETPOINT_22, current=21.0, mode=Mode.AUTO, cfg=cfg, state=state, budget=budget(), now=at
        ).action
        is Action.HELD_INTERVAL
    )
    assert (
        decide(
            SHED_URGENT, current=21.0, mode=Mode.AUTO, cfg=cfg, state=state, budget=budget(), now=at
        ).action
        is Action.WRITTEN
    )


def test_07f2_a_loads_own_interval_may_raise_the_floor_never_lower_it() -> None:
    """`command_min_interval` raises the kind's floor and never lowers it (§5.10)."""
    state = gate_state(last_write_at=NOW, last_value=21.0)
    at = NOW + timedelta(seconds=300)
    raised = gate_config(min_interval_s=120.0, command_min_interval_s=600.0)
    lowered = gate_config(min_interval_s=120.0, command_min_interval_s=10.0)
    assert (
        decide(
            SETPOINT_22,
            current=21.0,
            mode=Mode.AUTO,
            cfg=raised,
            state=state,
            budget=budget(),
            now=at,
        ).action
        is Action.HELD_INTERVAL
    )
    assert (
        decide(
            SETPOINT_22,
            current=21.0,
            mode=Mode.AUTO,
            cfg=lowered,
            state=state,
            budget=budget(),
            now=at,
        ).action
        is Action.WRITTEN
    )


def test_07g_row_7_dwell_holds_the_reversal_the_tank_made_four_times() -> None:
    """Row 7: the flipping tank, 75 → 45 → 75 → 45: four reversals in 23 minutes.

    `min_on_s` was configured on the tank and never consulted. A charge, once
    begun, is held for `min_on_s` unless the ceiling is at risk (stage ≥ 2,
    which arrives as `urgent`).
    """
    cfg = gate_config(min_interval_s=0.0, min_on_s=600.0, min_off_s=600.0)
    charging_since = gate_state(last_write_at=NOW, last_value=75.0, last_on_at=NOW)
    at = NOW + timedelta(seconds=300)
    down = Command(writes=(Write(Role.SETPOINT, 45.0),), reason="slot ended", want_on=False)
    assert (
        decide(
            down,
            current=75.0,
            mode=Mode.AUTO,
            cfg=cfg,
            state=charging_since,
            budget=budget(),
            now=at,
        ).action
        is Action.HELD_DWELL
    )
    urgent_down = Command(
        writes=(Write(Role.SETPOINT, 45.0),),
        reason="shed stage 2",
        urgent=True,
        want_on=False,
        sheds=True,
    )
    assert (
        decide(
            urgent_down,
            current=75.0,
            mode=Mode.AUTO,
            cfg=cfg,
            state=charging_since,
            budget=budget(),
            now=at,
        ).action
        is Action.WRITTEN
    )


@pytest.mark.inv("INV-58")
def test_08_row_8_seven_zwave_writes_in_a_minute_hold_the_seventh() -> None:
    """Item 8: six Z-Wave commands a minute is the budget; the seventh waits (INV-58).

    Six Heatit loops each inside their own 10-minute limit can still flood the
    mesh in one tick, which is why the bucket is per transport at site level and
    not per device.
    """
    cfg = gate_config(min_interval_s=0.0, transport=Transport.ZWAVE)
    bucket = budget()
    state = gate_state()
    for index in range(6):
        at = NOW + timedelta(seconds=index)
        decision = decide(
            Command(writes=(Write(Role.MODE_SELECT, f"opt{index}"),), reason="shed"),
            current="other",
            mode=Mode.AUTO,
            cfg=cfg,
            state=state,
            budget=bucket,
            now=at,
        )
        assert decision.action is Action.WRITTEN, index
        bucket = decision.budget

    seventh = decide(
        Command(writes=(Write(Role.MODE_SELECT, "opt7"),), reason="shed"),
        current="other",
        mode=Mode.AUTO,
        cfg=cfg,
        state=state,
        budget=bucket,
        now=NOW + timedelta(seconds=7),
    )
    assert seventh.action is Action.HELD_BUDGET

    blunt = decide(
        Command(writes=(Write(Role.MODE_SELECT, "opt7"),), reason="main fuse", blunt=True),
        current="other",
        mode=Mode.AUTO,
        cfg=cfg,
        state=state,
        budget=bucket,
        now=NOW + timedelta(seconds=7),
    )
    assert blunt.action is Action.WRITTEN, "a breaker beats a budget"


@pytest.mark.inv("INV-58")
def test_08b_the_bucket_refills_over_a_minute() -> None:
    """A token bucket is a sliding minute, not a counter that has to be reset."""
    cfg = gate_config(min_interval_s=0.0, transport=Transport.BLE)
    bucket = budget()
    for index in range(4):
        bucket = bucket.consume(Transport.BLE, NOW + timedelta(seconds=index))
    assert bucket.available(Transport.BLE, NOW + timedelta(seconds=5)) == 0
    # A sliding minute, not a counter that resets: at +61 s the writes at 0 s and
    # +1 s have aged out and the two after them have not.
    assert bucket.available(Transport.BLE, NOW + timedelta(seconds=61)) == 2
    assert bucket.available(Transport.BLE, NOW + timedelta(seconds=64)) == 4
    held = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(),
        budget=bucket,
        now=NOW + timedelta(seconds=5),
    )
    assert held.action is Action.HELD_BUDGET


@pytest.mark.inv("INV-24")
def test_07h_row_9_a_write_blocks_schedules_a_verify_and_spends_a_token() -> None:
    """Row 9: the write the executor performs blocks, is verified, and costs a token."""
    cfg = gate_config(verify_after_s=60.0, transport=Transport.ZWAVE)
    decision = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    assert decision.action is Action.WRITTEN
    assert decision.blocking is True, "a non-blocking call reports success for nothing (INV-24)"
    assert decision.command == SETPOINT_22
    assert decision.verify_at == NOW + timedelta(seconds=60)
    assert decision.gate.last_write_at == NOW
    assert decision.gate.last_value == 22.0
    assert decision.gate.verify_due == NOW + timedelta(seconds=60)
    assert decision.gate.last_on_at == NOW
    assert decision.budget.available(Transport.ZWAVE, NOW) == 5


@pytest.mark.inv("INV-22")
def test_07i_a_lost_write_is_re_issued_because_the_entity_is_the_witness() -> None:
    """INV-22: the next tick compares the grant with the read-back, not with memory.

    A Bluetooth write can be accepted by HA and never reach the device. The
    deviation is logged and counted, it is *not* a failure, and no explicit
    retry is needed: the entity still says 21 °C, so the same decision comes
    out again.
    """
    cfg = gate_config(min_interval_s=0.0, verify_after_s=30.0)
    written = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    later = NOW + timedelta(seconds=31)
    state, deviated = verify(written.gate, current=21.0, tolerance=cfg.tolerance, now=later)
    assert deviated
    assert state.deviations == 1
    assert state.failures == 0, "nothing refused us"
    assert state.verify_due is None

    again = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=state,
        budget=budget(),
        now=later,
    )
    assert again.action is Action.WRITTEN


@pytest.mark.inv("INV-22")
def test_07i3_a_read_back_older_than_the_write_is_not_a_read_back() -> None:
    """Row 3b past `verify_due`: one poll may predate the write, two cannot.

    The BLE charger's limit entity is what the last poll saw. Thirty seconds after
    a write the poll may not have run yet, and the entity still says 21 °C *with a
    stamp older than our write*: re-issuing on that is a duplicate write, not a
    decision against the read-back (D-0251). A stamp two polls old is a device
    that has stopped reporting, and row 3 judges it again.
    """
    cfg = gate_config(min_interval_s=0.0, verify_after_s=30.0)
    written = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    after_verify = NOW + timedelta(seconds=40)
    held = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=written.gate,
        budget=budget(),
        now=after_verify,
        current_at=NOW - timedelta(seconds=5),
    )
    assert held.action is Action.HELD_SETTLING
    assert "predates" in held.reason

    state, deviated = verify(
        written.gate,
        current=21.0,
        tolerance=cfg.tolerance,
        now=after_verify,
        current_at=NOW - timedelta(seconds=5),
    )
    assert not deviated, "no read-back yet, so no deviation"
    assert state.verify_due is not None, "the verify stays due"

    fresh = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=written.gate,
        budget=budget(),
        now=after_verify,
        current_at=NOW + timedelta(seconds=35),
    )
    assert fresh.action is Action.WRITTEN, (
        "a poll after the write that still says 21 is a lost write"
    )

    two_polls_late = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=written.gate,
        budget=budget(),
        now=NOW + timedelta(seconds=61),
        current_at=NOW - timedelta(seconds=5),
    )
    assert two_polls_late.action is Action.WRITTEN, "an entity silent for two polls is not lagging"


def test_07i2_a_verify_that_matches_is_silent() -> None:
    """A read-back that agrees clears the settle window and counts nothing."""
    cfg = gate_config(verify_after_s=30.0)
    written = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(),
        budget=budget(),
        now=NOW,
    )
    state, deviated = verify(
        written.gate, current=22.0, tolerance=cfg.tolerance, now=NOW + timedelta(seconds=31)
    )
    assert not deviated
    assert state.deviations == 0
    assert state.verify_due is None


def test_07j_same_compares_strings_case_insensitively() -> None:
    """The Heatit mode names differ across firmware; the comparison does not care."""
    assert same("Energy saving heating mode", "energy saving heating MODE", 0.0)
    assert not same("Heating mode", "Energy saving heating mode", 0.0)
    assert same(22.0, 22.04, 0.05)
    assert same(True, True, 0.0)
    assert not same(True, False, 0.0)


@pytest.mark.inv("INV-21")
def test_07k_the_matrix_is_ordered_first_hit_wins() -> None:
    """Every earlier row beats every later one: `same` beats interval, dwell and budget."""
    cfg = gate_config(min_interval_s=600.0, min_on_s=600.0, transport=Transport.CLOUD)
    exhausted = budget().consume(Transport.CLOUD, NOW).consume(Transport.CLOUD, NOW)
    decision = decide(
        SETPOINT_22,
        current=22.0,
        mode=Mode.AUTO,
        cfg=cfg,
        state=gate_state(last_write_at=NOW, last_value=22.0, last_on_at=NOW),
        budget=exhausted,
        now=NOW + timedelta(seconds=1),
    )
    assert decision.action is Action.SAME
