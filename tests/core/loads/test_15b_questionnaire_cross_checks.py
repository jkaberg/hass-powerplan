"""D4 §6 as sharpened for WP U.2: the checks one answer alone cannot make (review CTL-16).

`Questionnaire.validate` refused a number outside its own range and nothing
else: a floor loop could be told comfort 20 °C with a minimum of 25 °C, and a
heat pump's COP curve could fall as it got warmer. Both are refused at the
boundary now, with the field named (D8 §9 21 (e) - the flow half is
`tests/flows/test_logic_findings.py`).
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads.questionnaire import AnswerError, QCtx
from custom_components.powerplan.core.loads.types import base as device_types

CTX = QCtx(phases=3)


@pytest.mark.parametrize(
    ("type_key", "answers", "field"),
    [
        ("floor_heating", {"min_c": 25.0, "comfort_c": 20.0}, "comfort_c"),
        ("floor_heating", {"comfort_c": 29.0, "max_c": 27.0}, "comfort_c"),
        ("radiator", {"min_c": 22.0, "comfort_c": 20.0}, "comfort_c"),
        ("water_heater", {"ready_temp_c": 85.0, "max_c": 80.0}, "ready_temp_c"),
        ("water_heater", {"comfort_min_c": 60.0, "ready_temp_c": 55.0}, "ready_temp_c"),
        ("water_heater", {"legionella_temp_c": 70.0, "max_c": 65.0}, "legionella_temp_c"),
    ],
)
def test_a_comfort_outside_its_own_limits_is_refused_on_its_field(
    type_key: str, answers: dict[str, float], field: str
) -> None:
    """`min ≤ comfort ≤ max`, where the household answered both sides."""
    questionnaire = device_types.get(type_key).questionnaire

    with pytest.raises(AnswerError) as caught:
        questionnaire.validate(answers, CTX)

    assert (caught.value.key, caught.value.code) == (field, "outside_bounds")


def test_a_comfort_inside_its_limits_and_a_derived_limit_pass() -> None:
    """An unanswered limit is the type's own table, not an answer to argue with."""
    questionnaire = device_types.get("floor_heating").questionnaire

    assert questionnaire.validate({"min_c": 19.0, "comfort_c": 22.0, "max_c": 27.0}, CTX)
    assert questionnaire.validate({"comfort_c": 22.0}, CTX)


def test_a_cop_curve_that_falls_as_it_gets_warmer_is_refused() -> None:
    """Review CTL-13, CTL-16: a COP rises with the outdoor temperature."""
    questionnaire = device_types.get("heat_pump").questionnaire

    with pytest.raises(AnswerError) as caught:
        questionnaire.validate({"cop_curve": {-10.0: 3.8, 7.0: 2.1}}, CTX)

    assert (caught.value.key, caught.value.code) == ("cop_curve", "curve_not_rising")
    assert questionnaire.validate({"cop_curve": {-10.0: 2.1, 7.0: 3.8}}, CTX)


def test_preheat_temperature_is_asked_only_after_preheat() -> None:
    """Review HUB-17: the preheat limit follows the preheat answer (D8 §5.15 rule 5)."""
    questionnaire = device_types.get("heat_pump").questionnaire

    assert questionnaire.get("preheat_max_outdoor_c").asked_if == "preheat"
    assert questionnaire.get("preheat").kind.value == "bool"
