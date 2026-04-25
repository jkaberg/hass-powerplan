"""D4 §9 15 - questionnaire derivations, goldens and materialisation (INV-66, INV-67).

Golden answers → golden `Derived`, for **every registered type**. The goldens
live in `tests/golden/questionnaires/` (D9 §3) and are the readable record of
D4 §6.1–6.8: change a derivation table and the diff is the golden file, reviewed
like a preset. The parametrisation is the type registry itself, so a ninth type
cannot be added without one.

INV-66 is the one with teeth: derived values are **materialised** into the
subentry, so a release that improves a default never silently changes a running
house. The load keeps the numbers it was built with until the user re-derives,
and `rederive()` is what shows them the diff.
"""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path
from typing import Any

import pytest

from custom_components.powerplan.core.loads import device_types
from custom_components.powerplan.core.loads.questionnaire import (
    AnswerError,
    Option,
    QCtx,
    Question,
    QuestionKind,
    Questionnaire,
    explain,
    materialise,
    rederive,
)
from custom_components.powerplan.core.loads.types import floor_heating

GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "questionnaires"

#: Every type the registry offers - D4 §9 15 is "for every type" (D4 §6.1–6.8).
TYPES = device_types.keys()


def as_json(derived: Any) -> dict[str, Any]:
    """Return the golden shape: everything `Derived` carries, JSON-comparable."""
    return {
        "params": dict(derived.params),
        "strategy": derived.strategy,
        "strategy_params": dict(derived.strategy_params),
        "priority": derived.priority,
        "group": derived.group,
        "explanation_key": derived.explanation_key,
        "explanation_params": dict(derived.explanation_params),
        "derivation_version": derived.derivation_version,
    }


def ctx_from(golden: dict[str, Any]) -> QCtx:
    """Return the flow context the golden file was recorded with."""
    raw = golden.get("ctx", {})
    return QCtx(
        area=raw.get("area"),
        device_name=raw.get("device_name"),
        phases=raw.get("phases"),
        capabilities=frozenset(raw.get("capabilities", ())),
        readable=dict(raw.get("readable", {})),
    )


@pytest.mark.parametrize("type_key", TYPES)
def test_15_golden_answers_derive_the_golden_parameters(type_key: str) -> None:
    """Every number in D4 §6.1 and §6.2, pinned."""
    golden = json.loads((GOLDEN / f"{type_key}.json").read_text(encoding="utf-8"))
    device_type = device_types.get(type_key)
    ctx = ctx_from(golden)
    answers = device_type.questionnaire.validate(golden["answers"], ctx)
    assert answers.as_json() == golden["answers"], "the golden answers are already complete"
    derived = device_type.derive(answers, ctx)
    assert as_json(derived) == golden["derived"]


@pytest.mark.parametrize("type_key", TYPES)
def test_15b_every_question_has_a_default_and_advanced_is_never_required(type_key: str) -> None:
    """INV-65: a load is fully configurable without opening Advanced.

    A question either resolves its own default from the context, is one of the
    sliders D4 §6 pre-fills "from the above" (`derived_default`), or is optional
    by nature - an entity binding, or a second ready-by time (D4 §6.3), where
    "none" *is* the sane default. Nothing is required, and the empty
    questionnaire derives a working load.
    """
    optional_by_nature = {QuestionKind.ENTITY, QuestionKind.TIME}
    questionnaire = device_types.get(type_key).questionnaire
    for question in questionnaire.questions:
        assert (
            question.default_for(QCtx()) is not None
            or question.derived_default
            or question.kind in optional_by_nature
        ), question.key
    plain = questionnaire.validate({}, QCtx())
    derived = device_types.get(type_key).derive(plain, QCtx())
    assert derived.params, "defaults alone must derive a working load"
    assert derived.strategy in device_types.get(type_key).strategies


@pytest.mark.inv("INV-66")
def test_15c_materialise_stores_the_derivation_version() -> None:
    """The subentry carries the answers, the derived values and the version."""
    device_type = device_types.get("floor_heating")
    answers = device_type.questionnaire.validate({"room": "bathroom"}, QCtx())
    derived = device_type.derive(answers, QCtx())
    data = materialise("floor_heating", answers, derived)

    assert data["type"] == "floor_heating"
    assert data["derivation_version"] == derived.derivation_version
    assert data["params"]["comfort_c"] == pytest.approx(24.0)
    assert data["answers"]["room"] == "bathroom"
    assert json.loads(json.dumps(data)) == data, "a subentry holds JSON, not objects"


@pytest.mark.inv("INV-66")
def test_15d_changing_a_derivation_table_does_not_change_an_existing_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release that improves a default must not walk a house's bathroom.

    The load is built from what was materialised, so the only way the new table
    reaches it is the user asking - and then they are shown the diff.
    """
    device_type = device_types.get("floor_heating")
    answers = device_type.questionnaire.validate({"room": "bathroom"}, QCtx())
    stored = materialise("floor_heating", answers, device_type.derive(answers, QCtx()))
    assert stored["params"]["comfort_c"] == pytest.approx(24.0)

    warmer = dict(floor_heating.ROOM_DEFAULTS)
    warmer["bathroom"] = floor_heating.RoomDefaults(comfort_c=25.0, floor_c=21.0, priority=32)
    monkeypatch.setattr(floor_heating, "ROOM_DEFAULTS", warmer)

    assert stored["params"]["comfort_c"] == pytest.approx(24.0), "what was stored is what holds"

    fresh, diff = rederive(stored, device_type, QCtx())
    assert fresh.params["comfort_c"] == pytest.approx(25.0)
    assert diff["comfort_c"] == (24.0, 25.0), "re-derive shows a diff, it does not apply silently"


@pytest.mark.inv("INV-67")
def test_15e_the_review_step_reads_back_what_was_decided() -> None:
    """INV-67: derive *and explain* - a translation key plus its parameters."""
    device_type = device_types.get("floor_heating")
    answers = device_type.questionnaire.validate(
        {"room": "bathroom", "covering": "wood", "heating_type": "cable_in_screed"}, QCtx()
    )
    derived = device_type.derive(answers, QCtx())
    explanation = explain(answers, derived)

    assert explanation.key == "floor_heating_review"
    assert explanation.params["max_c"] == pytest.approx(27.0)
    assert explanation.params["room"] == "bathroom"
    assert explanation.params["substitutable"] is False


def test_15f_the_answers_are_a_boundary_and_are_validated_there() -> None:
    """Unknown keys, bad choices and out-of-range numbers are refused, not clamped."""
    questionnaire = device_types.get("floor_heating").questionnaire
    with pytest.raises(AnswerError, match="screed_depth_mm"):
        questionnaire.validate({"screed_depth_mm": "thick"}, QCtx())
    with pytest.raises(AnswerError, match="covering"):
        questionnaire.validate({"covering": "granite"}, QCtx())
    with pytest.raises(AnswerError, match="area_m2"):
        questionnaire.validate({"area_m2": -4.0}, QCtx())
    with pytest.raises(AnswerError, match="hair_colour"):
        questionnaire.validate({"hair_colour": "brown"}, QCtx())


def test_15g_a_defaulted_answer_may_be_a_function_of_what_ha_knows() -> None:
    """The room is prefilled from the HA area (HLD §7.9 point 2)."""
    questionnaire = device_types.get("floor_heating").questionnaire
    assert questionnaire.validate({}, QCtx(area="Bathroom"))["room"] == "bathroom"
    assert questionnaire.validate({}, QCtx(area="Loft"))["room"] == "other"
    assert questionnaire.validate({}, QCtx(area="Bad 1. etasje"))["room"] == "bathroom"
    assert device_types.get("ev").questionnaire.validate({}, QCtx(phases=3))["phases"] == "3"


def test_15h_the_framework_itself_answers_the_five_question_kinds() -> None:
    """D4 §4.6's `Question`/`Questionnaire`, against one of each kind.

    A `time` question has no consumer until the water heater's "ready by"
    (D4 §6.3), so the framework is exercised here rather than through a
    type that does not exist yet.
    """
    questionnaire = Questionnaire(
        questions=(
            Question(key="ready_by", kind=QuestionKind.TIME, default="06:30"),
            Question(key="litres", kind=QuestionKind.NUMBER, default=200.0, min=50.0, max=500.0),
            Question(key="legionella", kind=QuestionKind.BOOL, default=True),
            Question(
                key="control",
                kind=QuestionKind.CHOICE,
                options=(Option(value="thermostat"), Option(value="relay")),
                default="thermostat",
            ),
            Question(key="power_entity", kind=QuestionKind.ENTITY, default=None),
        )
    )
    assert questionnaire.keys() == ("ready_by", "litres", "legionella", "control", "power_entity")
    assert questionnaire.get("litres").unit is None
    assert questionnaire.defaults(QCtx())["ready_by"] == "06:30"

    answers = questionnaire.validate({"ready_by": "17:00", "legionella": False}, QCtx())
    assert answers["ready_by"] == time(17, 0)
    assert answers.number("litres") == pytest.approx(200.0)
    assert answers.flag("legionella") is False
    assert answers.choice("control") == "thermostat"
    assert answers.as_json()["ready_by"] == "17:00", "a subentry stores HH:MM, not a time object"

    with pytest.raises(AnswerError, match="ready_by"):
        questionnaire.validate({"ready_by": "half past six"}, QCtx())
    with pytest.raises(KeyError):
        questionnaire.get("no_such_question")
