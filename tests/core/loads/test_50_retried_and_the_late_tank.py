"""D4 §9 50 - the late tank (D-0710) and a device retried until it answers (D-0711).

The reference house's tank heated from 22:00 and was under its ready band at
06:00 on four mornings of nine; its charger and a heat pump drop writes for
seconds at a time (`design/reviews/field-audit-2026-10.md` §3, §4).
"""

from __future__ import annotations

from custom_components.powerplan.core.loads import device_types
from custom_components.powerplan.core.loads.questionnaire import QCtx, materialise
from custom_components.powerplan.core.strategies import params_of


def test_50_a_fresh_water_heater_derives_prefer_late() -> None:
    """Fresh answers carry `prefer_late: true`; a tank stored without it plans early."""
    device_type = device_types.get("water_heater")
    answers = device_type.questionnaire.validate({}, QCtx())
    derived = device_type.derive(answers, QCtx())
    assert derived.strategy == "deadline_fill"
    assert derived.strategy_params["prefer_late"] is True
    assert materialise("water_heater", answers, derived)["strategy_params"]["prefer_late"] is True

    # A subentry stored before D-0710 keeps its parameters (INV-66): the
    # strategy's own default stands until Change setup is saved.
    assert params_of("deadline_fill", {"min_block_min": 30.0})["prefer_late"] is False


def test_50b_a_device_that_stopped_answering_is_retried_without_end() -> None:
    """40 failures in a row: `unhealthy`, and the write still goes out once its interval is up."""
    from datetime import timedelta  # noqa: PLC0415

    from custom_components.powerplan.core.loads import Mode  # noqa: PLC0415
    from custom_components.powerplan.core.loads.gate import Action, decide  # noqa: PLC0415
    from tests.core.loads.conftest import NOW, budget, gate_config, gate_state  # noqa: PLC0415
    from tests.core.loads.test_07_write_gate_matrix import SETPOINT_22  # noqa: PLC0415

    state = gate_state(
        failures=40, last_error="timeout", last_write_at=NOW - timedelta(seconds=121)
    )
    assert state.unhealthy
    retry = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=gate_config(min_interval_s=120.0),
        state=state,
        budget=budget(),
        now=NOW,
    )
    assert retry.action is Action.WRITTEN

    # A heat pump that has ignored ten setpoints in a row gets the eleventh at its interval.
    ignored = gate_state(deviations=10, last_write_at=NOW - timedelta(seconds=901))
    again = decide(
        SETPOINT_22,
        current=21.0,
        mode=Mode.AUTO,
        cfg=gate_config(min_interval_s=900.0),
        state=ignored,
        budget=budget(),
        now=NOW,
    )
    assert again.action is Action.WRITTEN
