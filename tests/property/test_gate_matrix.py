"""Property: random write sequences through the pure gate decision (D9 §3).

The WriteGate is the one place in powerplan where a wrong number reaches a
charger, and D9 asks for 100 % coverage of `writegate.py` plus a property test
over random write sequences. That is only cheap because the decision matrix has
no Home Assistant in it (PLAN §7 dec. 5): what is generated here is a whole
day's worth of ticks - random desired values, random availability, random
urgency, random clocks - and what is asserted is the handful of properties the
matrix exists to guarantee, whatever order the rows are hit in.

The executor's own property (a service call per `written` decision and nothing
else) arrives with `writegate.py` in WP2.1 and leans on these.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from custom_components.powerplan.core.loads import Mode, Role
from custom_components.powerplan.core.loads.gate import (
    TRANSPORT_LIMIT_PER_MIN,
    Action,
    Command,
    GateConfig,
    GateState,
    Transport,
    TransportBudget,
    Write,
    decide,
    same,
)

OSLO = ZoneInfo("Europe/Oslo")
START = datetime(2026, 2, 3, 18, 7, 13, tzinfo=OSLO)

TOLERANCE = 0.05
WRITTEN = {Action.WRITTEN}

#: One tick: how far the clock moved, what we want, what the device holds,
#: whether the entity answers, and the two flags a kind may set.
ticks = st.lists(
    st.tuples(
        st.integers(min_value=1, max_value=400),  # seconds since the last tick
        st.floats(min_value=17.0, max_value=27.0, allow_nan=False),  # desired
        st.floats(min_value=17.0, max_value=27.0, allow_nan=False),  # what it holds
        st.booleans(),  # available
        st.booleans(),  # urgent
        st.booleans(),  # blunt
    ),
    min_size=1,
    max_size=60,
)


@settings(max_examples=200, deadline=None)
@given(
    ticks=ticks,
    mode=st.sampled_from(list(Mode)),
    transport=st.sampled_from(list(Transport)),
    min_interval_s=st.sampled_from([0.0, 30.0, 120.0, 600.0]),
    dwell_s=st.sampled_from([0.0, 600.0, 1800.0]),
)
def test_the_gate_matrix_holds_over_random_write_sequences(
    ticks: list[tuple[int, float, float, bool, bool, bool]],
    mode: Mode,
    transport: Transport,
    min_interval_s: float,
    dwell_s: float,
) -> None:
    """Six properties of D4 §5.10, over any sequence of ticks."""
    cfg = GateConfig(
        tolerance=TOLERANCE,
        min_interval_s=min_interval_s,
        verify_after_s=60.0,
        transport=transport,
        min_on_s=dwell_s,
        min_off_s=dwell_s,
    )
    state = GateState()
    budget = TransportBudget.empty()
    now = START
    written_at: list[datetime] = []

    for step_s, desired, held, available, urgent, blunt in ticks:
        now = now + timedelta(seconds=step_s)
        command = Command(
            writes=(Write(Role.SETPOINT, desired),),
            reason="property",
            urgent=urgent,
            blunt=blunt,
            want_on=desired > held,
        )
        before_state, before_budget = state, budget
        decision = decide(
            command,
            current=held,
            mode=mode,
            cfg=cfg,
            state=state,
            budget=budget,
            now=now,
            available=available,
        )

        # 1. The inputs are never mutated: the core is pure.
        assert before_state == state
        assert before_budget == budget

        # 2. A value the device already holds is never sent - INV-21, whatever
        #    `urgent` and `blunt` say. (In a mode that may not write at all,
        #    rows 1 and 2 answer first and say so more precisely.)
        if same(held, desired, TOLERANCE) and mode in {Mode.AUTO, Mode.FORCE}:
            assert decision.action is Action.SAME

        # 3. No write ever leaves in a mode that may not write.
        if mode in {Mode.OBSERVE, Mode.DELEGATED, Mode.OFF}:
            assert decision.action not in WRITTEN
            assert decision.command is None

        # 4. Only a `written` decision hands the executor a command, schedules a
        #    verify and moves the write clock.
        if decision.action is Action.WRITTEN:
            assert decision.command is command
            assert decision.blocking is True
            assert decision.verify_at == now + timedelta(seconds=cfg.verify_after_s)
            assert decision.gate.last_write_at == now
            assert decision.gate.last_value == pytest.approx(desired)
            # 6. INV-58: a non-blunt write only leaves when the transport's
            #    bucket had a token for it. Blunt sheds buy past the bucket -
            #    a breaker beats a budget - so they are not bounded, only
            #    counted.
            if not blunt:
                recent = sum(1 for at in written_at if (now - at).total_seconds() < 60.0)
                assert recent < TRANSPORT_LIMIT_PER_MIN[transport]
            written_at.append(now)
        else:
            assert decision.command is None
            assert decision.gate.last_write_at == state.last_write_at
            assert decision.gate.last_value == state.last_value

        # 5. The command interval binds unless the write is urgent or blunt.
        if (
            decision.action is Action.WRITTEN
            and state.last_write_at is not None
            and not (urgent or blunt)
        ):
            assert (now - state.last_write_at).total_seconds() >= cfg.min_interval_s

        state, budget = decision.gate, decision.budget


@settings(max_examples=100, deadline=None)
@given(
    desired=st.floats(min_value=17.0, max_value=27.0, allow_nan=False),
    held=st.floats(min_value=17.0, max_value=27.0, allow_nan=False),
    urgent=st.booleans(),
    blunt=st.booleans(),
)
def test_mode_force_buys_past_no_row(
    desired: float, held: float, urgent: bool, blunt: bool
) -> None:
    """A load in mode `force` passes through every row like any other (§5.10)."""
    cfg = GateConfig(
        tolerance=TOLERANCE, min_interval_s=600.0, verify_after_s=60.0, transport=Transport.ZWAVE
    )
    state = GateState(last_write_at=START, last_value=held)
    command = Command(
        writes=(Write(Role.SETPOINT, desired),), reason="force", urgent=urgent, blunt=blunt
    )
    at = START + timedelta(seconds=60)

    in_auto = decide(
        command,
        current=held,
        mode=Mode.AUTO,
        cfg=cfg,
        state=state,
        budget=TransportBudget.empty(),
        now=at,
    )
    in_force = decide(
        command,
        current=held,
        mode=Mode.FORCE,
        cfg=cfg,
        state=state,
        budget=TransportBudget.empty(),
        now=at,
    )
    assert in_force.action is in_auto.action
    assert in_force.gate == in_auto.gate
    assert in_force.command == in_auto.command
