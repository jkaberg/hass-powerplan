"""D4 §9 28 - whose change is a setpoint powerplan also writes (INV-27, D-0414).

`setpoint_origin` is the whole test: ours by the write's `Context` id or by the
value we last wrote, held while the write settles or before the record is
reconciled after a start, the household's otherwise. The setpoint-walk incident,
a restart reading the eco setpoint as a fresh target - is the `reconciled=False`
row: held, never adopted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.loads.gate import GateState, Origin, setpoint_origin

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
TOLERANCE = 0.05
OURS = GateState(
    last_write_at=NOW - timedelta(minutes=10), last_value=21.0, last_context_id="ctx-ours"
)


def _origin(
    state: GateState = OURS,
    *,
    value: float,
    context_id: str | None = "ctx-other",
    reconciled: bool = True,
) -> Origin:
    return setpoint_origin(
        state,
        value=value,
        context_id=context_id,
        tolerance=TOLERANCE,
        reconciled=reconciled,
        now=NOW,
    )


@pytest.mark.inv("INV-27")
def test_our_own_write_settling_is_ours_by_its_context() -> None:
    """The state our write caused carries its context: never adopted, whatever it says."""
    assert _origin(value=19.5, context_id="ctx-ours") is Origin.OURS


@pytest.mark.inv("INV-27")
def test_a_late_report_of_our_value_under_a_fresh_context_is_ours() -> None:
    """A sleepy thermostat reports our 21 °C after the context window: still ours."""
    assert _origin(value=21.02) is Origin.OURS


@pytest.mark.inv("INV-27")
def test_a_hand_on_the_dial_once_reconciled_is_the_households() -> None:
    """Any other value, any other context, the record reconciled: a user override."""
    assert _origin(value=23.0) is Origin.USER


@pytest.mark.inv("INV-27")
def test_a_value_seen_before_the_record_is_reconciled_is_held() -> None:
    """The setpoint-walk shape: at a restart, the eco setpoint is neither ours nor theirs."""
    assert _origin(GateState(), value=19.0, context_id=None, reconciled=False) is Origin.HELD


@pytest.mark.inv("INV-27")
def test_an_intermediate_report_while_our_write_settles_is_held() -> None:
    """A write still settling: a value between old and new is nobody's decision."""
    settling = GateState(
        last_write_at=NOW - timedelta(seconds=5),
        last_value=21.0,
        verify_due=NOW + timedelta(seconds=55),
        last_context_id="ctx-ours",
    )
    assert _origin(settling, value=22.5) is Origin.HELD


@pytest.mark.inv("INV-27")
def test_a_device_we_never_wrote_to_follows_its_household() -> None:
    """No write on record, reconciled: every change is the household's."""
    assert _origin(GateState(), value=22.0) is Origin.USER


# --- D-0497: every recent write is ours, a child of ours is ours, a person is a person ---


@pytest.mark.inv("INV-27")
def test_an_older_write_s_context_and_a_child_of_ours_are_ours() -> None:
    """A late report of the write before last, or an automation answering ours: never a hand."""
    from custom_components.powerplan.core.loads.gate import (  # noqa: PLC0415
        RECENT_CONTEXTS,
        remember_context,
    )

    state = remember_context(remember_context(OURS, "ctx-2"), "ctx-3")
    assert state.last_context_id == "ctx-3"
    assert state.recent_context_ids == ("ctx-2", "ctx-ours")
    assert (
        setpoint_origin(
            state, value=19.0, context_id="ctx-ours", tolerance=TOLERANCE, reconciled=True, now=NOW
        )
        is Origin.OURS
    )
    assert (
        setpoint_origin(
            state,
            value=19.0,
            context_id="ctx-new",
            parent_id="ctx-2",
            tolerance=TOLERANCE,
            reconciled=True,
            now=NOW,
        )
        is Origin.OURS
    )
    for i in range(RECENT_CONTEXTS + 2):
        state = remember_context(state, f"ctx-x{i}")
    assert len(state.recent_context_ids) == RECENT_CONTEXTS
    assert "ctx-ours" not in state.recent_context_ids


@pytest.mark.inv("INV-27")
def test_a_person_is_a_person_even_while_our_write_settles() -> None:
    """A `user_id` on the context: someone in the app - not held, not ours."""
    settling = GateState(
        last_write_at=NOW - timedelta(seconds=5),
        last_value=21.0,
        verify_due=NOW + timedelta(seconds=55),
        last_context_id="ctx-ours",
    )
    assert (
        setpoint_origin(
            settling,
            value=22.5,
            context_id="ctx-app",
            user_id="u1",
            tolerance=TOLERANCE,
            reconciled=True,
            now=NOW,
        )
        is Origin.USER
    )
    # Our own value from a person is no change at all.
    assert (
        setpoint_origin(
            settling,
            value=21.0,
            context_id="ctx-app",
            user_id="u1",
            tolerance=TOLERANCE,
            reconciled=True,
            now=NOW,
        )
        is Origin.OURS
    )
