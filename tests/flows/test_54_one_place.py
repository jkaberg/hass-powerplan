"""One place for a data point (D8 §9 54, D-0746).

The heat pump's outdoor and outlet sensors, the car's and the battery's charge
level, a tank's probes and every type's power meter were questions on the
questions step as well as roles on the match step: two places, and one of them
never bound. They are the data points step's now, and `derive()` reads them from
the bindings. A subentry stored before still binds its answer at setup
(`test_ev_soc_flow.py::test_a_car_saved_before_the_soc_answer_was_bound_is_bound_at_setup`).
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.questionnaire import QCtx
from custom_components.powerplan.core.loads.registry import device_types
from custom_components.powerplan.flow.load import (
    answers_from_bindings,
    data_point_answers,
    question_schema,
)
from custom_components.powerplan.providers.profiles.base import RoleBinding

DATA_POINT_KEYS = frozenset(
    {
        "power_entity",
        "soc_entity",
        "outdoor_entity",
        "outlet_entity",
        "temp_entity",
        "temp_top_entity",
        "temp_bottom_entity",
    }
)


def _fields(schema: object) -> set[str]:
    """Return every field of a form, its sections opened."""
    out: set[str] = set()
    for key, value in schema.schema.items():  # type: ignore[attr-defined]
        inner = getattr(value, "schema", None)
        if inner is not None and hasattr(inner, "schema"):
            out |= _fields(inner)
        else:
            out.add(str(key))
    return out


@pytest.mark.inv("INV-76")
@pytest.mark.parametrize("type_key", device_types.keys())
def test_54_no_questions_step_offers_a_data_point(type_key: str) -> None:
    """Each type's entity answers are the data points step's, never a question."""
    device_type = device_types.get(type_key)
    keys = {key for key, _role in data_point_answers(type_key)}
    asked = DATA_POINT_KEYS & {q.key for q in device_type.questionnaire.questions}
    assert asked == keys, "every data point answer of the questionnaire is the step's"

    form = question_schema(
        device_type.questionnaire, type_key, QCtx(phases=3), skip=frozenset(keys)
    )
    assert not _fields(form) & DATA_POINT_KEYS


def test_54_derive_reads_the_answers_from_the_bindings() -> None:
    """A bound role fills its answer; an unbound one leaves it out."""
    bound = (
        RoleBinding(Role.OUTDOOR_TEMP, "sensor.outside"),
        RoleBinding(Role.POWER, "sensor.heat_pump_power"),
    )
    assert answers_from_bindings("heat_pump", bound) == {"outdoor_entity": "sensor.outside"}
    assert answers_from_bindings("generic_switch", bound) == {
        "power_entity": "sensor.heat_pump_power"
    }
