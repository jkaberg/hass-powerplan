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
