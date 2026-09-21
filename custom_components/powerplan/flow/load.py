"""The load subentry flow (D8 §5.2, §5.4; D4 §4.6).

    device → match → questions → review → subentry
    reconfigure → questions (pre-filled) → review with the re-derive diff

Nothing is decided here: the profile registry says what the device is and
binds its roles (D4 §5.9), the device type's questionnaire asks in plain
language and `derive()` turns the answers into parameters (INV-66), and the
review renders `explain()` before anything is saved (INV-67). What the flow
adds is the rendering of D8 §5.4 and the record D8 §4 calls `LoadSubentryData`:
the answers, the derived parameters and their version, the bindings as the
profile made them, and the strategy and priority the household kept or changed.

Every step's labels come from `strings.json` under `config_subentries.load`
and the choice vocabularies from `selector.<type>_<key>`; the review's
sentence is assembled from `explain()`'s key and values, with the question
labels as its vocabulary, so the flow never shows an internal key (§5.4).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import time
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    EntityWithDeviceFilterSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    ObjectSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from custom_components.powerplan import doclinks
from custom_components.powerplan.const import (
    CONF_TARIFF,
    DOMAIN,
    ENTITY_SETTINGS,
    LOAD_BINDINGS,
    LOAD_DEVICE_ID,
    LOAD_MANUAL_OVERRIDES,
    LOAD_PARAMS,
    LOAD_PRIORITY,
    LOAD_PROFILE,
    LOAD_STRATEGY,
    LOAD_TITLE_USER_SET,
    LOAD_TYPE,
    PRIORITY_LEVELS,
    SECTION_ADVANCED,
    SUBENTRY_LOAD,
    priority_level,
)
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.questionnaire import (
    AnswerError,
    Answers,
    QCtx,
    Question,
    QuestionKind,
    explain,
    materialise,
    rederive,
)
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.core.tariffs import household
from custom_components.powerplan.flow.questionnaire import (
    advanced_section,
    as_duration,
    duration_selector,
    jsonable,
    kw_selector,
    percent_selector,
    seconds_of,
    time_selector,
)
from custom_components.powerplan.flow.text import Text, device_name, entity_name
from custom_components.powerplan.providers.profiles import registry as profiles
from custom_components.powerplan.providers.profiles.base import (
    DeviceView,
    MatchResult,
    RoleBinding,
    numeric_binding,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.loads.questionnaire import Derived, Questionnaire
    from custom_components.powerplan.core.loads.types.base import DeviceType

_LOGGER = logging.getLogger(__name__)

__all__ = ["LoadSubentryFlow", "binding_from_data", "binding_to_data", "load_title"]

#: Which `entity` question binds which domain (D8 §5.4 "domain filter from the Question").
_ENTITY_DOMAINS: dict[str, tuple[str, ...]] = {
    "arrival_sources": ("calendar",),
    "calendar_entity": ("calendar",),
    "never_switch": ("switch", "input_boolean", "binary_sensor"),
    "schedule_entity": ("schedule",),
}
_ENTITY_DEFAULT_DOMAINS: tuple[str, ...] = ("sensor",)
#: `entity` questions whose answer is more than one entity (D4 §4.4).
_MULTI_ENTITY_KEYS: frozenset[str] = frozenset({"arrival_sources", "never_switch"})
#: A duration's unit, in seconds: rendered as hours and minutes (review CTL-8).
_DURATION_UNITS: dict[str, float] = {"s": 1.0, "min": 60.0, "h": 3600.0}
#: A number in hours that is a daily quota, not an interval: a slider 1–24 h (CTL-4).
_QUOTA_KEYS: frozenset[str] = frozenset({"hours_per_day"})
_QUOTA_RANGE = (1.0, 24.0)
#: Where a derived-default question's value is found in `derive()`'s parameters,
#: when it is not under its own key, and the factor from the parameter to the
#: question's unit (HUB-19: the derived value shown as a suggestion).
_DERIVED_PARAM: dict[str, tuple[str, float]] = {
    "min_c": ("floor_c", 1.0),
    "screed_depth_mm": ("screed_mm", 1.0),
    "power_w": ("nameplate_w", 1.0),
    "duration_min": ("duration_s", 1 / 60),
}
#: The site's phase count answers a load's own phases question on a single-phase
#: site: one option is not a question (review CTL-14).
_SITE_PHASES_KEY = "phases"
#: The weekly-time question's seven day keys, Monday first (`Question.kind = weekly_time`).
_WEEKDAYS = tuple(str(day) for day in range(7))
#: The role-entity fields of the match step are prefixed so they never collide
#: with `type` and `profile`.
_ROLE_PREFIX = "role_"
#: What each role's picker offers: its domains and device classes, as entity
#: filters (review CTL-10). A `filter`, not the legacy `domain` key,
#: so a binding the profile made on another domain is never refused on submit.
_SENSOR = "sensor"
_ROLE_FILTERS: dict[Role, list[EntityWithDeviceFilterSelectorConfig]] = {
    Role.POWER: [{"domain": _SENSOR, "device_class": "power"}],
    Role.ENERGY: [{"domain": _SENSOR, "device_class": "energy"}],
    Role.SESSION_ENERGY: [{"domain": _SENSOR, "device_class": "energy"}],
    Role.TEMP: [{"domain": _SENSOR, "device_class": "temperature"}, {"domain": "climate"}],
    Role.TEMP_FLOOR: [{"domain": _SENSOR, "device_class": "temperature"}, {"domain": "climate"}],
    Role.OUTDOOR_TEMP: [{"domain": _SENSOR, "device_class": "temperature"}, {"domain": "weather"}],
    Role.OUTLET_TEMP: [{"domain": _SENSOR, "device_class": "temperature"}],
    Role.SETPOINT: [{"domain": ["climate", "water_heater", "number"]}],
    Role.ECO_SETPOINT: [{"domain": "number"}],
    Role.FLOOR_MIN: [{"domain": "number"}],
    Role.HYSTERESIS: [{"domain": "number"}],
    Role.MODE_SELECT: [{"domain": ["select", "climate"]}],
    Role.SWITCH: [{"domain": ["switch", "input_boolean", "light"]}],
    Role.ENABLE: [{"domain": ["switch", "input_boolean"]}],
    Role.CURRENT_SET: [{"domain": "number"}],
    Role.CURRENT_MAX: [{"domain": ["number", _SENSOR]}],
    Role.CIRCUIT_MAX: [{"domain": ["number", _SENSOR]}],
    Role.CABLE_RATING: [{"domain": ["number", _SENSOR]}],
    Role.CURRENT_L1: [{"domain": _SENSOR, "device_class": "current"}],
    Role.CURRENT_L2: [{"domain": _SENSOR, "device_class": "current"}],
    Role.CURRENT_L3: [{"domain": _SENSOR, "device_class": "current"}],
    Role.SOC: [{"domain": _SENSOR, "device_class": "battery"}],
    Role.CONNECTED: [{"domain": ["binary_sensor", _SENSOR]}],
    Role.DOOR: [{"domain": "binary_sensor"}],
    Role.STATUS: [{"domain": _SENSOR}],
    Role.BLOCKED_BY: [{"domain": _SENSOR}],
    Role.PROGRAM_STATE: [{"domain": _SENSOR}],
    Role.START: [{"domain": ["button", "switch"]}],
    Role.BATTERY_POWER_SET: [{"domain": "number"}],
    Role.BATTERY_MODE: [{"domain": "select"}],
    Role.SG_A: [{"domain": "switch"}],
    Role.SG_B: [{"domain": "switch"}],
}
#: Measurements every load can use whichever device they are on - a Z-Wave meter
#: on its own device measuring a heat pump: always offered, optional.
_ALWAYS_OFFERED: tuple[Role, ...] = (Role.POWER, Role.ENERGY)
#: A type's own questionnaire can ask for an optional entity the match step
#: never sees - the heat pump's outdoor/outlet sensors (D4 §5.14), the car's SoC
#: on the car's own device rather than the charger's (D4 §5.11), read off
#: whatever device they happen to live on, unlike a match-step role's own
#: device. `(question key, the role it becomes)`, by type.
_EXTRA_ROLE_ANSWERS: dict[str, tuple[tuple[str, Role], ...]] = {
    "heat_pump": (("outdoor_entity", Role.OUTDOOR_TEMP), ("outlet_entity", Role.OUTLET_TEMP)),
    "ev": (("soc_entity", Role.SOC),),
    "battery": (("soc_entity", Role.SOC),),
}


# --------------------------------------------------------------------------- #
# Bindings on the wire
# --------------------------------------------------------------------------- #


def binding_to_data(binding: RoleBinding) -> dict[str, Any]:
    """Return one binding as the subentry stores it (D8 §4, everything the entity said)."""
    return {
        "role": binding.role.value,
        "entity_id": binding.entity_id,
        "unit": binding.unit,
        "scale": binding.scale,
        "options": list(binding.options),
        "required": binding.required,
        "step": binding.step,
        "min_value": binding.min_value,
        "max_value": binding.max_value,
        "writable": binding.writable,
        "attribute": binding.attribute,
    }


def extra_bindings(
    hass: HomeAssistant, type_key: str, params: Mapping[str, Any], *, profile: str
) -> tuple[RoleBinding, ...]:
    """Bind a type's own optional off-device sensor answers (D4 §5.14), read-only.

    Unlike a match-step role, the entity did not come from the device the
    household picked - the profile it reached from never saw it, so there is
    no `MatchResult` binding to start from, only the answer itself. The flow
    calls it on every answer; setup calls it for a subentry saved before an
    answer was bound (D-0485).
    """
    out: list[RoleBinding] = []
    for key, role in _EXTRA_ROLE_ANSWERS.get(type_key, ()):
        entity_id = params.get(key)
        if not entity_id:
            continue
        view = DeviceView.from_states(hass, [str(entity_id)]).get(str(entity_id))
        if view is None:
            continue
        binding = numeric_binding(view, role, profile=profile)
        if binding is not None:
            out.append(binding)
    return tuple(out)


def binding_from_data(row: Mapping[str, Any]) -> RoleBinding:
    """Rebuild a binding from the subentry."""
    return RoleBinding(
        role=Role(str(row["role"])),
        entity_id=str(row["entity_id"]),
        unit=row.get("unit"),
        scale=float(row.get("scale", 1.0)),
        options=tuple(row.get("options") or ()),
        required=bool(row.get("required", False)),
        step=row.get("step"),
        min_value=row.get("min_value"),
        max_value=row.get("max_value"),
        writable=bool(row.get("writable", False)),
        attribute=row.get("attribute"),
    )


def load_title(hass: HomeAssistant, device_id: str | None, fallback: str) -> str:
    """Return the device's own name, or `fallback`."""
    return device_name(hass, device_id, fallback)


# --------------------------------------------------------------------------- #
# Questionnaire rendering (D8 §5.4)
# --------------------------------------------------------------------------- #


def _number_selector(question: Question) -> Any:
    """Return the control a number question gets (D8 §5.15 controls).

    °C a slider over the type's own range (CTL-5); % a slider step 1 (CTL-2);
    a daily quota of hours a slider 1–24 (CTL-4); an interval hours and minutes
    (CTL-8); watts in kW (CTL-15); everything else a box.
    """
    unit = question.unit
    if question.key in _QUOTA_KEYS:
        return NumberSelector(
            NumberSelectorConfig(
                min=_QUOTA_RANGE[0],
                max=_QUOTA_RANGE[1],
                step=0.5,
                unit_of_measurement="h",
                mode=NumberSelectorMode.SLIDER,
            )
        )
    if unit in _DURATION_UNITS:
        return duration_selector()
    if unit == "W" and question.min is not None and question.max is not None:
        return kw_selector(question.min, question.max)
    if unit == "%" and question.min is not None and question.max is not None:
        return percent_selector(low=question.min, high=question.max)
    slider = unit == "°C" and question.min is not None
    config = NumberSelectorConfig(
        mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
        step=0.5 if unit == "°C" else "any",
    )
    if question.min is not None:
        config["min"] = question.min
    if question.max is not None:
        config["max"] = question.max
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _curve_selector() -> Any:
    """Return a COP curve as a form list of {outdoor °C, COP} rows, never a text DSL (CTL-13)."""
    number = {"number": {"mode": "box", "step": "any"}}
    return ObjectSelector(
        ObjectSelectorConfig(
            fields={
                "outdoor_c": {
                    "selector": {"number": {**number["number"], "unit_of_measurement": "°C"}},
                    "required": True,
                },
                "cop": {"selector": number, "required": True},
            },
            multiple=True,
            label_field="outdoor_c",
            translation_key="cop_curve",
        )
    )


def option_key(value: str) -> str:
    """Return an option's translation key: a value like `1.5` is spelled `1_5` (hassfest's rule)."""
    return value.replace(".", "_")


def _selector(  # noqa: PLR0911 - one selector per kind, as D8 §5.4 tabulates them
    question: Question, type_key: str, current: Any = None, text: Text | None = None
) -> Any:
    """Return the selector D8 §5.4 gives for one question, with §5.15's controls."""
    match question.kind:
        case QuestionKind.CHOICE:
            vocabulary = f"{type_key}_{question.key}"
            values = [str(option.value) for option in question.options]
            # A value that cannot be a translation key (`1.5`) is labelled here,
            # from its slug's translation, in the system language (review R3).
            options: list[Any] = (
                values
                if text is None or all(option_key(value) == value for value in values)
                else [
                    SelectOptionDict(
                        value=value, label=text.word(vocabulary, option_key(value)) or value
                    )
                    for value in values
                ]
            )
            return SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=f"{type_key}_{question.key}",
                    sort=False,
                )
            )
        case QuestionKind.NUMBER:
            return _number_selector(question)
        case QuestionKind.BOOL:
            return BooleanSelector()
        case QuestionKind.TIME:
            return time_selector(current)
        case QuestionKind.ENTITY:
            multiple = question.key in _MULTI_ENTITY_KEYS
            return EntitySelector(
                EntitySelectorConfig(
                    domain=list(_ENTITY_DOMAINS.get(question.key, _ENTITY_DEFAULT_DOMAINS)),
                    multiple=multiple,
                )
            )
        case QuestionKind.CURVE:
            return _curve_selector()
        case _:
            return TextSelector()


def _form_default(question: Question, value: Any) -> Any:  # noqa: PLR0911 - one shape per kind
    """Return `value` in the shape its selector accepts."""
    if value is None:
        return None
    if question.kind is QuestionKind.TIME:
        return value.strftime("%H:%M") if isinstance(value, time) else str(value)[:5]
    if question.kind is QuestionKind.CURVE:
        if isinstance(value, Mapping):
            return [
                {"outdoor_c": float(x), "cop": float(y)}
                for x, y in sorted((float(x), float(y)) for x, y in value.items())
            ]
        return value
    if question.kind is QuestionKind.ENTITY and question.key in _MULTI_ENTITY_KEYS:
        return list(value) if isinstance(value, list | tuple) else [value]
    if question.kind is QuestionKind.NUMBER:
        if question.key not in _QUOTA_KEYS and question.unit in _DURATION_UNITS:
            return as_duration(float(value) * _DURATION_UNITS[question.unit])
        if question.unit == "W" and question.min is not None and question.max is not None:
            return float(value) / 1000.0
        return float(value)
    return value


def _from_form(question: Question, value: Any) -> Any:
    """Return one submitted value in the question's own unit - the inverse of `_form_default`."""
    if value is None:
        return None
    if question.kind is QuestionKind.NUMBER:
        if question.key not in _QUOTA_KEYS and question.unit in _DURATION_UNITS:
            seconds = seconds_of(value)
            return None if seconds is None else seconds / _DURATION_UNITS[question.unit]
        if question.unit == "W" and question.min is not None and question.max is not None:
            return float(value) * 1000.0
    return value


def _same(question: Question, answer: Any, suggestion: Any) -> bool:
    """Whether a submitted value is the suggestion it was shown, in the question's own unit."""
    mine, theirs = _from_form(question, answer), _from_form(question, suggestion)
    if isinstance(mine, int | float) and isinstance(theirs, int | float):
        return math.isclose(float(mine), float(theirs), rel_tol=1e-9, abs_tol=1e-9)
    return bool(mine == theirs)


def _marker(key: str, default: Any) -> Any:
    return vol.Optional(key) if default is None else vol.Optional(key, default=default)


def suggestions(
    questionnaire: Questionnaire, device_type: DeviceType, ctx: QCtx, values: Mapping[str, Any]
) -> dict[str, Any]:
    """Return what `derive()` makes of a derived-default question left blank (review HUB-19).

    The number the household would get, shown as the field's suggested value
    rather than "leave empty to derive"; nothing when the answers so far do not
    derive (the form then shows the fields empty, as before).
    """
    try:
        derived = device_type.derive(questionnaire.validate(dict(values), ctx), ctx)
    except AnswerError, KeyError, TypeError, ValueError:
        return {}
    found: dict[str, Any] = {}
    for question in questionnaire.questions:
        if not question.derived_default or values.get(question.key) is not None:
            continue
        param, factor = _DERIVED_PARAM.get(question.key, (question.key, 1.0))
        value = derived.params.get(param)
        if value is None:
            continue
        if isinstance(value, int | float) and not isinstance(value, bool):
            value = float(value) * factor
        found[question.key] = _form_default(question, value)
    return found


def _asked(question: Question, ctx: QCtx, *, followups: bool) -> bool:
    """Whether this form asks `question`: follow-ups on their own step, a single option never."""
    if (question.asked_if is not None) != followups:
        return False
    if question.needs is not None and question.needs not in ctx.capabilities:
        return False
    return not (question.key == _SITE_PHASES_KEY and ctx.phases == 1)


def question_schema(
    questionnaire: Questionnaire,
    type_key: str,
    ctx: QCtx,
    values: Mapping[str, Any] | None = None,
    *,
    suggested: Mapping[str, Any] | None = None,
    followups: bool = False,
    skip: frozenset[str] = frozenset(),
    text: Text | None = None,
    meters: Mapping[Role, str | None] | None = None,
) -> vol.Schema:
    """Render the questionnaire as one form; advanced questions in a collapsed section (INV-65).

    `skip` leaves out what an entity owns once the appliance exists (D8 §5.16).
    `meters` - a reconfigure's power and energy bindings - adds their pickers
    under Avansert: the match step that offers them on an add is not revisited
    while the device exists (D-0486).

    A question that follows a yes/no answer (`asked_if`) is on the follow-up
    step, not here (`followups=True` renders that step; D8 §5.15 rule 5); a value
    derived at run time is the field's suggested value (HUB-19).
    """
    given = values or {}
    hints = suggested or {}
    fields: dict[Any, Any] = {}
    hidden: dict[Any, Any] = {}
    for question in questionnaire.questions:
        if question.key in skip or not _asked(question, ctx, followups=followups):
            continue
        current = given.get(question.key, question.default_for(ctx))
        target = hidden if question.advanced else fields
        if question.kind is QuestionKind.WEEKLY_TIME:
            table = dict(current or {})
            for day in _WEEKDAYS:
                clock = table.get(day)
                target[_marker(f"{question.key}_{day}", clock)] = time_selector(clock)
            continue
        default = _form_default(question, current)
        if default is None and question.key in hints:
            marker: Any = vol.Optional(
                question.key, description={"suggested_value": hints[question.key]}
            )
        else:
            marker = _marker(question.key, default)
        target[marker] = _selector(question, type_key, default, text)
    for role, entity_id in (meters or {}).items():
        picker = EntitySelector(EntitySelectorConfig(filter=_ROLE_FILTERS.get(role, [{}])))
        hidden[_marker(f"{_ROLE_PREFIX}{role.value}", entity_id)] = picker
    if hidden:
        fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
    return vol.Schema(fields)


def _flat(user_input: Mapping[str, Any]) -> dict[str, Any]:
    merged = {key: value for key, value in user_input.items() if key != SECTION_ADVANCED}
    merged.update(user_input.get(SECTION_ADVANCED) or {})
    return merged


def _parse_curve(raw: Any) -> Any:
    """Form rows `[{outdoor_c, cop}]` → `{-10.0: 2.1, 7.0: 3.8}`; a stored mapping passes.

    A row the validator cannot read goes to it as is, so the error is its own
    (`not_a_curve`), on the field.
    """
    if raw is None or isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, list | tuple):
        return raw
    if not raw:
        return None
    points: dict[Any, Any] = {}
    for row in raw:
        if not isinstance(row, Mapping) or "outdoor_c" not in row or "cop" not in row:
            return raw
        points[row["outdoor_c"]] = row["cop"]
    return points


def answers_from_form(
    questionnaire: Questionnaire,
    user_input: Mapping[str, Any],
    suggested: Mapping[str, Any] | None = None,
    *,
    ctx: QCtx | None = None,
    followups: bool = False,
) -> dict[str, Any]:
    """Return the raw answers the questionnaire validates, out of the submitted form.

    Only the questions that form asked (`question_schema`'s own rule). A field
    left at the value it was suggested is not an answer: the derivation runs
    again from the other answers, so changing the room still changes the comfort
    it suggested (HUB-19, D-0395).
    """
    flat = _flat(user_input)
    hints = suggested or {}
    raw: dict[str, Any] = {}
    for question in questionnaire.questions:
        if not _asked(question, ctx or QCtx(), followups=followups):
            continue
        if question.kind is QuestionKind.WEEKLY_TIME:
            table = {
                day: _hhmm(flat[f"{question.key}_{day}"])
                for day in _WEEKDAYS
                if flat.get(f"{question.key}_{day}") not in (None, "")
            }
            raw[question.key] = table
            continue
        if question.key not in flat:
            continue
        value = flat[question.key]
        if value in ("", []):
            value = None
        if question.key in hints and _same(question, value, hints[question.key]):
            continue
        if question.kind is QuestionKind.CURVE:
            value = _parse_curve(value)
        elif question.kind is QuestionKind.TIME and isinstance(value, str):
            value = _hhmm(value)
        elif question.kind is QuestionKind.ENTITY and question.key in _MULTI_ENTITY_KEYS:
            value = tuple(value or ())
        else:
            value = _from_form(question, value)
        raw[question.key] = value
    return raw


def _hhmm(value: Any) -> str:
    """`"07:00:00"` → `"07:00"`; a `time` likewise."""
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value)
    return text[:5] if len(text) >= 5 and text[2] == ":" else text  # noqa: PLR2004 - HH:MM


# --------------------------------------------------------------------------- #
# The review (INV-67)
# --------------------------------------------------------------------------- #


#: Where a derived parameter's label is found, in order: the question that asked
#: it, the review's own field for it, and the load vocabulary for what no screen
#: asks (NEW-3).
_LABELS = (
    "config_subentries.load.step.questions.data.{key}",
    "config_subentries.load.step.questions.sections.advanced.data.{key}",
    "config_subentries.load.step.review.sections.advanced.data.param_{key}",
    "selector.load_text.options.label_{key}",
)
#: The types whose shadow holds a comfort target on its own thermostat (D11 §6).
_THERMOSTATS = frozenset({"floor_heating", "radiator", "heat_pump"})
#: Below this a match is a guess, and the match step says so (review LOAD-2).
UNSURE_BELOW = 0.6


def param_label(text: Text, key: str) -> str:
    """Return a derived parameter's label in the language (never its key, NEW-3)."""
    for path in _LABELS:
        label = text.string(path.format(key=key))
        if label:
            return label
    return key.replace("_", " ")


def _value(text: Text, type_key: str, key: str, value: Any) -> str:  # noqa: PLR0911 - one form per kind
    """Return one parameter's value in words: a choice by its label, a number for the language."""
    if value is None:
        return text.word("text", "none")
    if isinstance(value, bool):
        return text.yes_no(value)
    if isinstance(value, int | float):
        return text.number(value)
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, str):
        for path in (
            f"selector.{type_key}_{key}.options.{value}",
            f"selector.{key}.options.{value}",
            f"selector.load_text.options.{key}_{value}",
        ):
            label = text.string(path)
            if label:
                return label
        return value
    if isinstance(value, Mapping):
        parts = [
            f"{text.string(f'config_subentries.load.step.questions.data.{key}_{day}') or day} "
            f"{_value(text, type_key, key, item)}"
            for day, item in value.items()
        ]
        return ", ".join(parts) or text.word("text", "none")
    if isinstance(value, list | tuple):
        return ", ".join(_value(text, type_key, key, item) for item in value) or text.word(
            "text", "none"
        )
    return str(value)


def explanation_text(text: Text, derived: Derived, answers: Answers, type_key: str) -> str:
    """Render `explain()` as one paragraph in the user's language (INV-67; review NEW-3).

    The type by its name, then every value the derivation thought worth a
    sentence, each under its own label and in words - a choice by its label, a
    flag as yes or no, a number for the language - and D11 §6's shadow
    sentence. Nothing here is an internal key; every word is a translation.
    """
    explanation = explain(answers, derived)
    parts = [
        f"{param_label(text, key)}: {_value(text, type_key, key, value)}"
        for key, value in explanation.params.items()
    ]
    sentence = f"{text.word('load_type', type_key)} — " + "; ".join(parts) + "."
    shadow = _shadow_sentence(text, type_key, derived.params)
    return f"{sentence} {shadow}" if shadow else sentence


def _shadow_sentence(text: Text, type_key: str, params: Mapping[str, Any]) -> str | None:
    """Say in plain words what D11's shadow bills savings against (D11 §6).

    One sentence per store kind, keyed by the type the way `store_kind_of`
    settles it for every load this table names. A relay with no daily quota - an
    on-call appliance such as a sauna - has none: PowerPlan can only pause and
    restore it, not say what it would otherwise have cost.
    """
    if type_key == "generic_switch":
        key = (
            "shadow_generic_switch"
            if params.get("hours_per_day") is not None
            else ("shadow_generic_switch_none")
        )
        return text.word("load_text", key)
    if type_key in _THERMOSTATS:
        comfort_c = params.get("comfort_c")
        target = (
            f"{text.number(comfort_c)} °C"
            if isinstance(comfort_c, int | float)
            else text.word("load_text", "its_target")
        )
        return text.word("load_text", f"shadow_{type_key}", target=target)
    return text.word("load_text", f"shadow_{type_key}") or None


def strategy_choices(device_type: DeviceType) -> list[str]:
    """Return the strategies a household chooses between: `always` left out (D-0412)."""
    return [key for key in device_type.strategies if key != "always"]


def _review_schema(
    derived: Derived,
    device_type: DeviceType,
    *,
    name: str,
    params: Mapping[str, Any] | None = None,
    reconfigure: bool = False,
) -> vol.Schema:
    """Name, strategy and priority when adding - and the derived parameters, editable, in Advanced.

    Strategy is asked only where there are two to choose between, priority as
    low/normal/high (D8 §5.16, D-0411, D-0412). On reconfigure both are the
    entities' now, as is every parameter `ENTITY_SETTINGS` names, so none is
    shown here (amended INV-66).
    """
    given = params if params is not None else derived.params
    fields: dict[Any, Any] = {vol.Required("name", default=name): TextSelector()}
    owned: frozenset[str] = frozenset()
    if reconfigure:
        owned = ENTITY_SETTINGS.get(device_type.key, frozenset())
    else:
        choices = strategy_choices(device_type)
        if len(choices) >= 2:  # noqa: PLR2004 - a choice needs two
            default = derived.strategy if derived.strategy in choices else choices[0]
            fields[vol.Optional("strategy", default=default)] = SelectSelector(
                SelectSelectorConfig(
                    options=choices,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="strategy",
                    sort=False,
                )
            )
        fields[vol.Optional("priority", default=priority_level(derived.priority))] = SelectSelector(
            SelectSelectorConfig(
                options=list(PRIORITY_LEVELS),
                mode=SelectSelectorMode.LIST,
                translation_key="priority_level",
                sort=False,
            )
        )
    hidden: dict[Any, Any] = {}
    for key, value in sorted(given.items()):
        if key in owned:
            continue
        if isinstance(value, bool):
            hidden[vol.Optional(f"param_{key}", default=value)] = BooleanSelector()
        elif isinstance(value, int | float):
            shown, selector = _param_control(key, value)
            hidden[vol.Optional(f"param_{key}", default=shown)] = selector
    if hidden:
        fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
    return vol.Schema(fields)


def _param_control(key: str, value: float) -> tuple[Any, Any]:
    """Return a derived number's shown value and control: watts in kW, seconds as a duration.

    Stored as they were - W and seconds (CTL-8, CTL-15); the suffix is the
    parameter's own unit, which D4 §6's tables keep in every key.
    """
    if key.endswith("_w"):
        return float(value) / 1000.0, NumberSelector(
            NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any", unit_of_measurement="kW")
        )
    if key.endswith("_s"):
        return as_duration(float(value)), duration_selector()
    return float(value), NumberSelector(
        NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
    )


def _param_value(key: str, shown: Any) -> float:
    """Return a review field's answer in the parameter's own unit (the inverse of `_param_control`)."""
    if key.endswith("_w"):
        return float(shown) * 1000.0
    if key.endswith("_s"):
        return float(seconds_of(shown) or 0.0)
    return float(shown)


def _params_from_review(
    base: Mapping[str, Any], user_input: Mapping[str, Any], shown: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Fold the Advanced edits over `base`; name the parameters the household changed.

    An edit is a value that differs from what the form *showed* (`shown`, else
    `base`): a field left as it was is not an edit, whatever `base` holds.
    """
    flat = _flat(user_input)
    displayed = shown if shown is not None else base
    params = dict(base)
    manual: list[str] = []
    for key, value in displayed.items():
        edited = flat.get(f"param_{key}")
        if edited is None or key not in params:
            continue
        new: bool | int | float
        if isinstance(value, bool):
            new = bool(edited)
        elif isinstance(value, int):
            new = round(_param_value(key, edited))
        elif isinstance(value, float):
            new = _param_value(key, edited)
        else:
            continue
        if isinstance(new, float) and math.isclose(new, float(value), abs_tol=1e-9):
            continue
        if new != value:
            params[key] = new
            manual.append(key)
    return params, manual


# --------------------------------------------------------------------------- #
# The device list (review CTL-11)
# --------------------------------------------------------------------------- #

#: A device PowerPlan can steer has something to write: HA's `DeviceSelector`
#: cannot say so, nor exclude an integration or mark a device (D8 §5.15 H8).
_CONTROL_DOMAINS: Final = frozenset(
    {"switch", "climate", "water_heater", "number", "select", "button"}
)


def added_devices(entry: ConfigEntry, *, but: str | None = None) -> set[str]:
    """Return the devices already an appliance of this home, `but` one being re-bound."""
    return {
        str(subentry.data.get(LOAD_DEVICE_ID))
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_LOAD
        and subentry.data.get(LOAD_DEVICE_ID)
        and subentry.subentry_id != but
    }


#: The review's answers that bind a load to its own grid tariff or the grid's switch (D4 §5.16).
GRID_TARIFF: Final = "grid_tariff"
SWITCHED: Final = "switched"
_NONE: Final = "none"
#: Device types a per-load grid tariff is pre-selected for (§14a's controllable devices).
_TARIFFED_BY_DEFAULT: Final = frozenset({"heat_pump", "ev"})


def grid_binding_fields(
    price: household.HouseholdPrice | None,
    text: Text,
    type_key: str,
    stored: Mapping[str, Any] | None = None,
) -> dict[Any, Any]:
    """Return the review's own-tariff and HDO pickers, only where the site's copy has either.

    "Har dette apparatet egen måler eller egen nettleie?" - the keys of the copy's
    `per_load` tariffs, pre-selected for a heat pump or a charger (§14a); the
    copy's HDO codes and "unknown" for a controlled circuit whose times are not
    published (D4 §5.16, D13 §18 G13–G15).
    """
    if price is None:
        return {}
    grid = price.grid
    stored = stored or {}
    none = SelectOptionDict(value=_NONE, label=text.word("text", "none"))
    fields: dict[Any, Any] = {}
    if grid.per_load:
        suggested = grid.per_load[0].key if type_key in _TARIFFED_BY_DEFAULT else _NONE
        fields[vol.Optional(GRID_TARIFF, default=stored.get(GRID_TARIFF) or suggested)] = (
            SelectSelector(
                SelectSelectorConfig(
                    options=[
                        none,
                        *(SelectOptionDict(value=row.key, label=row.name) for row in grid.per_load),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                    sort=False,
                )
            )
        )
    if grid.switched:
        fields[vol.Optional(SWITCHED, default=stored.get(SWITCHED) or _NONE)] = SelectSelector(
            SelectSelectorConfig(
                options=[
                    none,
                    *(
                        SelectOptionDict(value=row.key, label=f"{row.name} ({row.key})")
                        for row in grid.switched
                    ),
                    SelectOptionDict(
                        value=household.UNKNOWN_WINDOWS, label=text.word("text", "unknown_times")
                    ),
                ],
                mode=SelectSelectorMode.DROPDOWN,
                sort=False,
            )
        )
    return fields


def device_options(hass: HomeAssistant, text: Text, added: set[str]) -> list[SelectOptionDict]:
    """Return the devices an appliance can be, by name - never PowerPlan's own (CTL-11).

    A device qualifies with one switch, climate, water-heater, number, select or
    button entity. One already added is listed with a mark, so the household sees
    why it is refused rather than wondering where it went.
    """
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    own = {entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)}
    options: list[SelectOptionDict] = []
    # Iterating the registry yields entries on current HA and ids on the 2026.3
    # floor, where `.values()` is not yet deprecated; read both.
    for item in devices.devices:
        device = item if isinstance(item, dr.DeviceEntry) else devices.async_get(str(item))
        if device is None:
            continue
        if device.config_entries & own or device.disabled_by is not None:
            continue
        if not any(
            entity.domain in _CONTROL_DOMAINS and entity.disabled_by is None
            for entity in er.async_entries_for_device(entities, device.id)
        ):
            continue
        name = device.name_by_user or device.name or device.id
        label = text.word("load_text", "already_added", name=name) if device.id in added else name
        options.append(SelectOptionDict(value=device.id, label=label))
    options.sort(key=lambda option: text.sort_key(option["label"]))
    return options


# --------------------------------------------------------------------------- #
# The flow
# --------------------------------------------------------------------------- #


class LoadSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one load (D8 §5.2)."""

    def __init__(self) -> None:
        """Start empty; every step fills one attribute."""
        self._device_id: str | None = None
        self._view: DeviceView | None = None
        self._matches: tuple[MatchResult, ...] = ()
        self._profile: str | None = None
        self._type: str | None = None
        self._bindings: tuple[RoleBinding, ...] = ()
        self._ctx: QCtx | None = None
        self._answers: Answers | None = None
        self._derived: Derived | None = None
        self._stored: Mapping[str, Any] | None = None
        #: What the questions step suggested for its derived fields (HUB-19).
        self._suggested: dict[str, Any] = {}
        #: The questions step's answers while a follow-up is asked.
        self._raw: dict[str, Any] = {}

    # ----------------------------------------------------------------- helpers

    def async_show_form(self, **kwargs: Any) -> SubentryFlowResult:
        """Show a step with its section linked: the type's page once a type is chosen (D14 §5.4)."""
        docs = doclinks.step_placeholders("load", str(kwargs.get("step_id")), self._type)
        kwargs["description_placeholders"] = {
            **docs,
            **(kwargs.get("description_placeholders") or {}),
        } or None
        return super().async_show_form(**kwargs)

    @property
    def _device_type(self) -> DeviceType:
        assert self._type is not None
        return device_types.get(self._type)

    def _qctx(self) -> QCtx:
        """Return the context defaults are read against: the HA area, the device, the site's phases."""
        assert self._device_id is not None
        device = dr.async_get(self.hass).async_get(self._device_id)
        area = None
        if device is not None and device.area_id:
            entry = ar.async_get(self.hass).async_get_area(device.area_id)
            area = entry.name if entry is not None else None
        electrical = dict(self._get_entry().data.get("electrical") or {})
        best = self._matches[0] if self._matches else None
        return QCtx(
            area=area,
            device_name=load_title(self.hass, self._device_id, self._device_id),
            phases=int(electrical.get("phases", 1)) if electrical else None,
            capabilities=best.capabilities if best is not None else frozenset(),
        )

    def _chosen(self, values: Mapping[str, Any]) -> MatchResult:
        """Return the match the household is looking at: its pick, else the best."""
        chosen = str(values.get("profile") or self._matches[0].profile)
        return next((m for m in self._matches if m.profile == chosen), self._matches[0])

    @staticmethod
    def _roles(match: MatchResult) -> tuple[list[Role], list[Role]]:
        """Return the required roles and the optional ones, the measurements always among them."""
        bound = {binding.role: binding for binding in match.bindings}
        required = [
            role
            for role in [*bound, *match.missing]
            if role in match.missing or bound[role].required
        ]
        optional = [role for role in bound if role not in required]
        optional += [role for role in _ALWAYS_OFFERED if role not in bound and role not in required]
        return list(dict.fromkeys(required)), optional

    def _match_schema(self, values: Mapping[str, Any] | None = None) -> vol.Schema:
        """Type, profile and the roles, each an entity picker filtered to fit (§5.2).

        The required roles in the form, the optional ones under Avansert (review
        LOAD-6) - the power and energy meters always among them, so a meter on
        another device can be bound. The profile is asked only when
        more than one claims the device (CTL-14).
        """
        given = _flat(values or {})
        match = self._chosen(given)
        profile = profiles.get(match.profile)
        type_options = [key for key in device_types.keys() if key in profile.types] or list(  # noqa: SIM118 - `keys()` is the registry's function
            device_types.keys()
        )
        default_type = str(given.get("type") or match.suggested_type or type_options[0])
        fields: dict[Any, Any] = {}
        # The type is asked first (LOAD-3); a re-bound device keeps the appliance's
        # (D8 §5.16). Only a flow that has none yet asks it here.
        if self._type is None:
            fields[vol.Required("type", default=default_type)] = SelectSelector(
                SelectSelectorConfig(
                    options=type_options,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="load_type",
                    sort=False,
                )
            )
        if len(self._matches) > 1:
            fields[vol.Required("profile", default=match.profile)] = SelectSelector(
                SelectSelectorConfig(
                    options=[m.profile for m in self._matches],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="load_profile",
                    sort=False,
                )
            )
        bound = {binding.role: binding for binding in match.bindings}
        required, optional = self._roles(match)
        hidden: dict[Any, Any] = {}
        for role in [*required, *optional]:
            binding = bound.get(role)
            key = f"{_ROLE_PREFIX}{role.value}"
            default = given.get(key, binding.entity_id if binding is not None else None)
            if role in required:
                marker: Any = vol.Required(key, default=default) if default else vol.Required(key)
            else:
                marker = _marker(key, default)
            picker = EntitySelector(EntitySelectorConfig(filter=_ROLE_FILTERS.get(role, [{}])))
            (fields if role in required else hidden)[marker] = picker
        if hidden:
            fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
        return vol.Schema(fields)

    def _bindings_from_match(
        self, user_input: Mapping[str, Any]
    ) -> tuple[tuple[RoleBinding, ...], tuple[Role, ...]]:
        """Return the bindings as the profile made them, with the household's entity changes."""
        answers = _flat(user_input)
        match = self._chosen(answers)
        by_role = {binding.role: binding for binding in match.bindings}
        out: list[RoleBinding] = []
        missing: list[Role] = []
        wanted = set(by_role) | set(match.missing) | set(_ALWAYS_OFFERED)
        for role in wanted:
            entity_id = answers.get(f"{_ROLE_PREFIX}{role.value}")
            binding = by_role.get(role)
            if not entity_id:
                if role in match.missing or (binding is not None and binding.required):
                    missing.append(role)
                continue
            if binding is not None and binding.entity_id == entity_id:
                out.append(binding)
                continue
            rebuilt = self._rebind(role, str(entity_id), binding, match.profile)
            if rebuilt is None:
                missing.append(role)
            else:
                out.append(rebuilt)
        return tuple(out), tuple(missing)

    def _rebind(
        self, role: Role, entity_id: str, previous: RoleBinding | None, profile: str
    ) -> RoleBinding | None:
        """Bind `role` to an entity the household chose instead, reading its own numbers."""
        registry = er.async_get(self.hass)
        entry = registry.async_get(entity_id)
        view = (
            self._view.get(entity_id)
            if self._view is not None and self._view.get(entity_id) is not None
            else DeviceView.from_states(self.hass, [entity_id]).get(entity_id)
        )
        if view is None:
            return None
        del entry
        return numeric_binding(
            view,
            role,
            profile=profile,
            writable=previous.writable if previous is not None else False,
            required=previous.required if previous is not None else False,
            attribute=previous.attribute if previous is not None else None,
        )

    def _extra_bindings(self, type_key: str, params: Mapping[str, Any]) -> tuple[RoleBinding, ...]:
        """Bind a type's own optional off-device sensor answers (D4 §5.14), read-only."""
        return extra_bindings(self.hass, type_key, params, profile=self._profile or "")

    # -------------------------------------------------------------------- user

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Ask "Hva vil du styre?" - all eight types in the household's words (LOAD-3, S9)."""
        if user_input is not None:
            self._type = str(user_input["type"])
            return await self.async_step_device()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("type", default=self._type or "ev"): SelectSelector(
                        SelectSelectorConfig(
                            options=list(device_types.keys()),
                            mode=SelectSelectorMode.LIST,
                            translation_key="load_type",
                            sort=False,
                        )
                    )
                }
            ),
            last_step=False,
        )

    async def _device_form(
        self, step_id: str, errors: Mapping[str, str], *, but: str | None = None
    ) -> SubentryFlowResult:
        """Show the flow's own device list (CTL-11)."""
        text = await Text.load(self.hass)
        options = device_options(self.hass, text, added_devices(self._get_entry(), but=but))
        placeholders = {"type": text.word("load_type", str(self._type))}
        if self.source == "reconfigure":
            placeholders["load"] = self._get_reconfigure_subentry().title
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("device"): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN, sort=False
                        )
                    )
                }
            ),
            errors=dict(errors) or None,
            description_placeholders=placeholders,
            last_step=False,
        )

    def _pick(self, device_id: str, *, but: str | None = None) -> str | None:
        """Match the picked device for the chosen type; return an error key, or `None`."""
        if device_id in added_devices(self._get_entry(), but=but):
            return "already_configured"
        view = DeviceView.from_hass(self.hass, device_id)
        matches = tuple(
            match
            for match in profiles.match(view)
            if self._type is None or self._type in profiles.get(match.profile).types
        )
        if not matches:
            return "no_profile"
        self._device_id, self._view, self._matches = device_id, view, matches
        return None

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the device from the flow's own list; detection confirms the type (CTL-11)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._pick(str(user_input["device"]))
            if error is None:
                return await self.async_step_match()
            errors["device"] = error
        return await self._device_form("device", errors)

    async def async_step_match(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show what the device is: the suggested type, the profile, the roles (D4 §5.9)."""
        errors: dict[str, str] = {}
        text = await Text.load(self.hass)
        placeholders: dict[str, str] = {"roles": text.word("text", "none")}
        best = self._matches[0]
        if user_input is not None:
            bindings, missing = self._bindings_from_match(user_input)
            if missing:
                errors["base"] = "role_missing"
                placeholders["roles"] = text.join(
                    text.string(
                        f"config_subentries.{SUBENTRY_LOAD}.step.match.data.{_ROLE_PREFIX}{role.value}"
                    )
                    or role.value.replace("_", " ")
                    for role in missing
                )
            else:
                self._profile = self._chosen(_flat(user_input)).profile
                self._type = str(user_input.get("type", self._type))
                self._bindings = bindings
                return await self.async_step_questions()
        placeholders.update(self._match_sentence(text, best))
        return self.async_show_form(
            step_id="match",
            data_schema=self._match_schema(user_input),
            errors=errors or None,
            description_placeholders=placeholders,
            last_step=False,
        )

    def _match_sentence(self, text: Text, best: MatchResult) -> dict[str, str]:
        """Say what the device looks like and what PowerPlan controls it with, in words (LOAD-2).

        Never the profile's key, an entity id or the profile's English reasons: the
        type by its name, the control by the household's own name for it, and a
        plain warning below `UNSURE_BELOW`.
        """
        device = load_title(self.hass, self._device_id, text.word("text", "none"))
        profile = profiles.get(best.profile)
        kind = self._type or best.suggested_type or next(iter(profile.types), None)
        control = next((b for b in best.bindings if b.writable), None) or next(
            iter(best.bindings), None
        )
        return {
            "device": device,
            "kind": text.word("load_type", kind) if kind else device,
            "control": device if control is None else entity_name(self.hass, control.entity_id),
            "warning": text.word("load_text", "unsure") if best.confidence < UNSURE_BELOW else "",
        }

    def _followups_asked(self, raw: Mapping[str, Any]) -> bool:
        """Whether any follow-up question's yes/no was answered yes (D8 §5.15 rule 5)."""
        questionnaire = self._device_type.questionnaire
        return any(
            question.asked_if is not None and bool(raw.get(question.asked_if))
            for question in questionnaire.questions
        )

    async def _answered(self, raw: Mapping[str, Any]) -> SubentryFlowResult | None:
        """Validate and derive; on success go on to the review, else return `None`."""
        device_type = self._device_type
        assert self._ctx is not None
        self._answers = device_type.questionnaire.validate(raw, self._ctx)
        # `derive()` refuses what no question alone can - a tank on a relay with
        # nothing to keep it safe (`unsafe_switch`, INV-64) - and that refusal is
        # an answer's error too (review NEW-2).
        self._derived = device_type.derive(self._answers, self._ctx)
        # Only a role this answer actually names is replaced - an unanswered
        # `outdoor_entity` leaves the match step's own auto-detected
        # outdoor_temp binding standing (D4 §5.14).
        extra = self._extra_bindings(str(self._type), self._derived.params)
        answered_roles = {binding.role for binding in extra}
        self._bindings = (
            *(b for b in self._bindings if b.role not in answered_roles),
            *extra,
        )
        if self.source == "reconfigure":
            return await self.async_step_reconfigure_review()
        return await self.async_step_review()

    def _entity_settings(self) -> frozenset[str]:
        """Return what an entity owns for this appliance: empty while adding it."""
        if self.source != "reconfigure":
            return frozenset()
        return ENTITY_SETTINGS.get(str(self._type), frozenset())

    def _stored_answers(self) -> dict[str, Any]:
        return dict(self._stored.get("answers") or {}) if self._stored is not None else {}

    async def async_step_questions(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask the type's questionnaire, rendered dynamically (D8 §5.4).

        A question that follows a yes/no (the heat pump's preheat limit) is
        asked on `questions_followup`, and only after a yes (HUB-17).
        """
        device_type = self._device_type
        questionnaire = device_type.questionnaire
        self._ctx = self._ctx or self._qctx()
        errors: dict[str, str] = {}
        values: dict[str, Any] = self._stored_answers()
        # A setting an entity owns is not asked again: its answer stands (D8 §5.16).
        skip = self._entity_settings()
        if user_input is not None:
            if self.source == "reconfigure":
                self._bindings = self._meters_from_form(user_input)
            raw = {
                **{key: value for key, value in values.items() if key in skip},
                **answers_from_form(questionnaire, user_input, self._suggested, ctx=self._ctx),
            }
            # A follow-up keeps what it had until it is asked again.
            self._raw = {
                **{
                    q.key: values[q.key]
                    for q in questionnaire.questions
                    if q.asked_if and q.key in values
                },
                **raw,
            }
            if self._followups_asked(self._raw):
                return await self.async_step_questions_followup()
            try:
                done = await self._answered(self._raw)
            except AnswerError as err:
                errors[err.key] = err.code
                values = raw
            else:
                assert done is not None
                return done
        self._suggested = suggestions(questionnaire, device_type, self._ctx, values)
        return self.async_show_form(
            step_id="questions",
            data_schema=question_schema(
                questionnaire,
                str(self._type),
                self._ctx,
                values,
                suggested=self._suggested,
                skip=skip,
                text=await Text.load(self.hass),
                meters=self._meters() if self.source == "reconfigure" else None,
            ),
            errors=errors or None,
            description_placeholders=await self._about(),
            last_step=False,
        )

    def _meters(self) -> dict[Role, str | None]:
        """Return the power and energy meters bound now, `None` for an unbound one."""
        bound = {binding.role: binding.entity_id for binding in self._bindings}
        return {role: bound.get(role) for role in _ALWAYS_OFFERED}

    def _meters_from_form(self, user_input: Mapping[str, Any]) -> tuple[RoleBinding, ...]:
        """Return the bindings with the reconfigure's meter answers applied (D-0486).

        A new entity is bound read-only off its own unit; a cleared picker
        unbinds the meter; one the entity cannot be read as stays as it was.
        """
        answers = _flat(user_input)
        by_role = {binding.role: binding for binding in self._bindings}
        for role in _ALWAYS_OFFERED:
            entity_id = answers.get(f"{_ROLE_PREFIX}{role.value}")
            previous = by_role.get(role)
            if not entity_id:
                if previous is not None and not previous.required:
                    by_role.pop(role)
            elif previous is None or previous.entity_id != entity_id:
                rebuilt = self._rebind(role, str(entity_id), previous, self._profile or "")
                if rebuilt is not None:
                    by_role[role] = rebuilt
        return tuple(by_role.values())

    async def _about(self) -> dict[str, str]:
        """Return the questions' title data: "Om {name}" - the device's name - and the type (LOAD-5)."""
        text = await Text.load(self.hass)
        kind = text.word("load_type", str(self._type))
        return {"type": kind, "name": load_title(self.hass, self._device_id, kind)}

    async def async_step_questions_followup(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask what follows a yes - the preheat limit after "preheat" (D8 §5.15 rule 5)."""
        device_type = self._device_type
        questionnaire = device_type.questionnaire
        assert self._ctx is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            raw = {
                **self._raw,
                **answers_from_form(questionnaire, user_input, ctx=self._ctx, followups=True),
            }
            try:
                done = await self._answered(raw)
            except AnswerError as err:
                errors[err.key] = err.code
            else:
                assert done is not None
                return done
        return self.async_show_form(
            step_id="questions_followup",
            data_schema=question_schema(
                questionnaire,
                str(self._type),
                self._ctx,
                self._raw,
                followups=True,
                text=await Text.load(self.hass),
            ),
            errors=errors or None,
            description_placeholders=await self._about(),
            last_step=False,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """`explain()`, the derived parameters in Advanced, strategy and priority (INV-67)."""
        assert self._answers is not None
        assert self._derived is not None
        device_type = self._device_type
        if user_input is not None:
            params, manual = _params_from_review(self._derived.params, user_input)
            data = self._materialise(user_input, params, manual)
            return self.async_create_entry(
                title=str(user_input["name"]),
                data=data,
                unique_id=f"{SUBENTRY_LOAD}:{self._device_id}",
            )
        text = await Text.load(self.hass)
        return self.async_show_form(
            step_id="review",
            data_schema=_review_schema(
                self._derived,
                device_type,
                name=load_title(
                    self.hass, self._device_id, text.word("load_type", str(self._type))
                ),
            ).extend(grid_binding_fields(self._site_price(), text, str(self._type))),
            description_placeholders={
                "explanation": explanation_text(
                    text, self._derived, self._answers, str(self._type)
                ),
                "strategy": text.word("strategy", self._derived.strategy) or self._derived.strategy,
            },
            last_step=True,
        )

    def _priority(self, user_input: Mapping[str, Any]) -> int:
        """Return the derived number where its level was kept, else the chosen level's (D-0422)."""
        assert self._derived is not None
        level = str(user_input.get("priority") or priority_level(self._derived.priority))
        if level == priority_level(self._derived.priority):
            return self._derived.priority
        return PRIORITY_LEVELS[level]

    def _materialise(
        self, user_input: Mapping[str, Any], params: Mapping[str, Any], manual: Sequence[str]
    ) -> dict[str, Any]:
        """Return the subentry's data (D8 §4 `LoadSubentryData`, INV-66)."""
        assert self._answers is not None
        assert self._derived is not None
        data = materialise(str(self._type), self._answers, self._derived)
        data[LOAD_PARAMS] = {key: jsonable(value) for key, value in sorted(params.items())}
        stored = self._stored or {}
        data[LOAD_STRATEGY] = str(
            user_input.get("strategy") or stored.get(LOAD_STRATEGY) or self._derived.strategy
        )
        data[LOAD_PRIORITY] = int(stored.get(LOAD_PRIORITY, self._priority(user_input)))
        data[LOAD_PROFILE] = self._profile
        data[LOAD_DEVICE_ID] = self._device_id
        data[LOAD_BINDINGS] = [binding_to_data(binding) for binding in self._bindings]
        data[LOAD_MANUAL_OVERRIDES] = list(manual)
        data["zone"] = None
        data["circuit"] = None
        for key in (GRID_TARIFF, SWITCHED):
            chosen = user_input.get(key, stored.get(key))
            data[key] = None if chosen in {None, _NONE} else str(chosen)
        return data

    def _site_price(self) -> household.HouseholdPrice | None:
        """Return the site's tariff copy, for the review's tariff bindings (D4 §5.16)."""
        raw = (self._get_entry().data.get(CONF_TARIFF) or {}).get("price")
        try:
            return household.from_json(raw) if raw else None
        except KeyError, ValueError, TypeError:
            return None

    # ------------------------------------------------------------- reconfigure

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask the same questions, pre-filled from the subentry (D8 §5.2)."""
        subentry = self._get_reconfigure_subentry()
        self._stored = dict(subentry.data)
        self._type = str(self._stored[LOAD_TYPE])
        self._profile = self._stored.get(LOAD_PROFILE)
        self._device_id = self._stored.get(LOAD_DEVICE_ID)
        self._bindings = tuple(
            binding_from_data(row) for row in self._stored.get(LOAD_BINDINGS) or ()
        )
        if not self._device_id or dr.async_get(self.hass).async_get(self._device_id) is None:
            # The hardware is gone (`device_missing`): pick its replacement,
            # bind its roles, then the same questions (D8 §5.16).
            return await self.async_step_reconfigure_device()
        self._view = DeviceView.from_hass(self.hass, self._device_id)
        self._matches = profiles.match(self._view)
        return await self.async_step_questions(user_input)

    async def async_step_reconfigure_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the device that replaces a removed one; its roles are matched next."""
        but = self._get_reconfigure_subentry().subentry_id
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._pick(str(user_input["device"]), but=but)
            if error is None:
                return await self.async_step_match()
            errors["device"] = error
        return await self._device_form("reconfigure_device", errors, but=but)

    def _levels_line(self, text: Text, params: Mapping[str, Any]) -> str:
        """Say what the device page now owns, read back, not offered to edit (D8 §5.16)."""
        assert self._stored is not None
        type_key = str(self._type)
        strategy = str(self._stored.get(LOAD_STRATEGY) or "")
        review = f"config_subentries.{SUBENTRY_LOAD}.step.review.data"
        level = priority_level(int(self._stored.get(LOAD_PRIORITY, PRIORITY_LEVELS["normal"])))
        parts = [
            f"{text.string(f'{review}.strategy')}: {text.word('strategy', strategy) or strategy}",
            f"{text.string(f'{review}.priority')}: {text.word('priority_level', level)}",
        ]
        parts.extend(
            f"{param_label(text, key)}: {_value(text, type_key, key, params[key])}"
            for key in sorted(ENTITY_SETTINGS.get(type_key, frozenset()))
            if key in params
        )
        return " · ".join(parts)

    def _device_link(self) -> dict[str, str]:
        """Return the appliance's device as a link to its page (D8 §5.16, §9 31)."""
        device = dr.async_get(self.hass).async_get(str(self._device_id))
        if device is None:
            return {"device_name": "—", "device_url": "/config/devices/dashboard"}
        return {
            "device_name": device.name_by_user or device.name or "—",
            "device_url": f"/config/devices/device/{device.id}",
        }

    async def async_step_reconfigure_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Re-derive from the answers, show the diff, keep or apply (INV-66)."""
        assert self._stored is not None
        assert self._answers is not None
        assert self._derived is not None
        device_type = self._device_type
        type_key = str(self._type)
        stored_params = dict(self._stored.get(LOAD_PARAMS) or {})
        previous_manual = list(self._stored.get(LOAD_MANUAL_OVERRIDES) or [])
        candidate = {**self._stored, "answers": self._answers.as_json(), LOAD_PARAMS: stored_params}
        fresh, diff = rederive(candidate, device_type, self._ctx or self._qctx())
        if user_input is not None:
            # The form showed the re-derived values; an edit is what differs from them.
            if user_input.get("rederive", True):
                params, manual = _params_from_review(fresh.params, user_input)
            else:
                params, manual = _params_from_review(stored_params, user_input, shown=fresh.params)
                manual = sorted(set(manual) | set(previous_manual))
            # What an entity owns is never re-derived: its value stands (amended INV-66).
            owned = ENTITY_SETTINGS.get(type_key, frozenset())
            params.update({key: stored_params[key] for key in owned if key in stored_params})
            manual = sorted({*manual, *(key for key in previous_manual if key in owned)})
            data = self._materialise(user_input, params, manual)
            data[LOAD_DERIVATION_VERSION_KEY] = fresh.derivation_version
            subentry = self._get_reconfigure_subentry()
            title = str(user_input["name"])
            # A name the household typed stops following the device's (§5.16).
            data[LOAD_TITLE_USER_SET] = bool(
                self._stored.get(LOAD_TITLE_USER_SET) or title != subentry.title
            )
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                unique_id=f"{SUBENTRY_LOAD}:{self._device_id}",
                title=title,
                data=data,
            )
        text = await Text.load(self.hass)
        owned = ENTITY_SETTINGS.get(type_key, frozenset())
        diff = {key: change for key, change in diff.items() if key not in owned}
        diff_lines = [
            f"{param_label(text, key)}: {_value(text, type_key, key, old)} → "
            f"{_value(text, type_key, key, new)}"
            for key, (old, new) in diff.items()
        ]
        subentry = self._get_reconfigure_subentry()
        schema = _review_schema(fresh, device_type, name=subentry.title, reconfigure=True).extend(
            {
                vol.Optional("rederive", default=True): BooleanSelector(),
                **grid_binding_fields(self._site_price(), text, type_key, self._stored),
            }
        )
        return self.async_show_form(
            step_id="reconfigure_review",
            data_schema=schema,
            description_placeholders={
                "explanation": explanation_text(text, fresh, self._answers, type_key),
                "diff": "\n".join(f"- {line}" for line in diff_lines) or text.word("text", "none"),
                "manual": text.join(param_label(text, key) for key in previous_manual)
                or text.word("text", "none"),
                "levels": self._levels_line(text, stored_params),
                **self._device_link(),
            },
            last_step=True,
        )


LOAD_DERIVATION_VERSION_KEY = "derivation_version"
