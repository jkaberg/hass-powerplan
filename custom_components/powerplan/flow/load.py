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
from collections.abc import Mapping
from datetime import time
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    DeviceSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)
from homeassistant.helpers.translation import async_get_translations

from custom_components.powerplan.const import (
    DOMAIN,
    LOAD_BINDINGS,
    LOAD_DEVICE_ID,
    LOAD_MANUAL_OVERRIDES,
    LOAD_PARAMS,
    LOAD_PRIORITY,
    LOAD_PROFILE,
    LOAD_STRATEGY,
    LOAD_TYPE,
    SECTION_ADVANCED,
    SUBENTRY_LOAD,
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
from custom_components.powerplan.flow.questionnaire import advanced_section, jsonable
from custom_components.powerplan.providers.profiles import registry as profiles
from custom_components.powerplan.providers.profiles.base import (
    DeviceView,
    MatchResult,
    RoleBinding,
    numeric_binding,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.loads.questionnaire import Derived, Questionnaire
    from custom_components.powerplan.core.loads.types.base import DeviceType

_LOGGER = logging.getLogger(__name__)

__all__ = ["LoadSubentryFlow", "binding_from_data", "binding_to_data", "load_title"]

#: Which `entity` question binds which domain (D8 §5.4 "domain filter from the Question").
_ENTITY_DOMAINS: dict[str, tuple[str, ...]] = {
    "calendar_entity": ("calendar",),
    "never_switch": ("switch", "input_boolean", "binary_sensor"),
}
_ENTITY_DEFAULT_DOMAINS: tuple[str, ...] = ("sensor",)
#: Units rendered as a slider rather than a box (D8 §5.4).
_SLIDER_UNITS = frozenset({"°C", "%"})
#: The weekly-time question's seven day keys, Monday first (`Question.kind = weekly_time`).
_WEEKDAYS = tuple(str(day) for day in range(7))
#: The role-entity fields of the match step are prefixed so they never collide
#: with `type` and `profile`.
_ROLE_PREFIX = "role_"


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
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    if device is None:
        return fallback
    return device.name_by_user or device.name or fallback


# --------------------------------------------------------------------------- #
# Questionnaire rendering (D8 §5.4)
# --------------------------------------------------------------------------- #


def _selector(question: Question, type_key: str) -> Any:  # noqa: PLR0911 - one selector per kind, as D8 §5.4 tabulates them
    """Return the selector D8 §5.4 gives for one question."""
    match question.kind:
        case QuestionKind.CHOICE:
            return SelectSelector(
                SelectSelectorConfig(
                    options=[option.value for option in question.options],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=f"{type_key}_{question.key}",
                    sort=False,
                )
            )
        case QuestionKind.NUMBER:
            slider = question.unit in _SLIDER_UNITS and question.min is not None
            config = NumberSelectorConfig(
                mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
                step=0.5 if question.unit == "°C" else "any",
            )
            if question.min is not None:
                config["min"] = question.min
            if question.max is not None:
                config["max"] = question.max
            if question.unit is not None:
                config["unit_of_measurement"] = question.unit
            return NumberSelector(config)
        case QuestionKind.BOOL:
            return BooleanSelector()
        case QuestionKind.TIME:
            return TimeSelector()
        case QuestionKind.ENTITY:
            multiple = question.key == "never_switch"
            return EntitySelector(
                EntitySelectorConfig(
                    domain=list(_ENTITY_DOMAINS.get(question.key, _ENTITY_DEFAULT_DOMAINS)),
                    multiple=multiple,
                )
            )
        case QuestionKind.CURVE:
            return TextSelector()
        case _:
            return TextSelector()


def _form_default(question: Question, value: Any) -> Any:  # noqa: PLR0911 - one shape per kind
    """Return `value` in the shape its selector accepts."""
    if value is None:
        return None
    if question.kind is QuestionKind.TIME:
        return value.strftime("%H:%M:%S") if isinstance(value, time) else str(value)
    if question.kind is QuestionKind.CURVE:
        if isinstance(value, Mapping):
            return ", ".join(f"{float(x):g}:{float(y):g}" for x, y in sorted(value.items()))
        return str(value)
    if question.kind is QuestionKind.ENTITY and question.key == "never_switch":
        return list(value) if isinstance(value, list | tuple) else [value]
    if question.kind is QuestionKind.NUMBER:
        return float(value)
    return value


def _marker(key: str, default: Any) -> Any:
    return vol.Optional(key) if default is None else vol.Optional(key, default=default)


def question_schema(
    questionnaire: Questionnaire, type_key: str, ctx: QCtx, values: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Render the questionnaire as one form; advanced questions in a collapsed section (INV-65)."""
    given = values or {}
    fields: dict[Any, Any] = {}
    hidden: dict[Any, Any] = {}
    for question in questionnaire.questions:
        current = given.get(question.key, question.default_for(ctx))
        target = hidden if question.advanced else fields
        if question.kind is QuestionKind.WEEKLY_TIME:
            table = dict(current or {})
            for day in _WEEKDAYS:
                target[_marker(f"{question.key}_{day}", table.get(day))] = TimeSelector()
            continue
        target[_marker(question.key, _form_default(question, current))] = _selector(
            question, type_key
        )
    if hidden:
        fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
    return vol.Schema(fields)


def _flat(user_input: Mapping[str, Any]) -> dict[str, Any]:
    merged = {key: value for key, value in user_input.items() if key != SECTION_ADVANCED}
    merged.update(user_input.get(SECTION_ADVANCED) or {})
    return merged


def _parse_curve(raw: Any) -> Any:
    """`"-10:2.1, 7:3.8"` → `{-10.0: 2.1, 7.0: 3.8}`; anything else goes to the validator as is."""
    if not isinstance(raw, str):
        return raw
    if not raw.strip():
        return None
    points: dict[float, float] = {}
    for part in raw.split(","):
        if ":" not in part:
            return raw
        x, y = part.split(":", 1)
        try:
            points[float(x)] = float(y)
        except ValueError:
            return raw
    return points


def answers_from_form(
    questionnaire: Questionnaire, user_input: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the raw answers the questionnaire validates, out of the submitted form."""
    flat = _flat(user_input)
    raw: dict[str, Any] = {}
    for question in questionnaire.questions:
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
        if question.kind is QuestionKind.CURVE:
            value = _parse_curve(value)
        elif question.kind is QuestionKind.TIME and isinstance(value, str):
            value = _hhmm(value)
        elif question.kind is QuestionKind.ENTITY and question.key == "never_switch":
            value = tuple(value or ())
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


def explanation_text(
    derived: Derived, answers: Answers, labels: Mapping[str, str], type_name: str
) -> str:
    """Render `explain()` as one paragraph in the user's language.

    The key names the type; the values are what the derivation thought worth a
    sentence, listed with the question labels as vocabulary. A derived value
    that is a table (the departures) is spelled out; a number keeps at most two
    decimals; nothing here is an internal key.
    """
    explanation = explain(answers, derived)
    parts: list[str] = []
    for key, value in explanation.params.items():
        label = labels.get(key, key.replace("_", " "))
        parts.append(f"{label}: {_pretty(value)}")
    return f"{type_name} — " + "; ".join(parts) + "."


def _pretty(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, Mapping):
        return ", ".join(f"{k}: {_pretty(v)}" for k, v in value.items()) or "—"
    if isinstance(value, list | tuple):
        return ", ".join(_pretty(v) for v in value) or "—"
    return str(value)


def _review_schema(
    derived: Derived,
    device_type: DeviceType,
    *,
    name: str,
    strategy: str | None = None,
    priority: int | None = None,
    params: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Name, strategy, priority - and the derived parameters, editable, in Advanced."""
    given = params if params is not None else derived.params
    fields: dict[Any, Any] = {
        vol.Required("name", default=name): TextSelector(),
        vol.Optional("strategy", default=strategy or derived.strategy): SelectSelector(
            SelectSelectorConfig(
                options=list(device_type.strategies),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="strategy",
                sort=False,
            )
        ),
        vol.Optional("priority", default=priority if priority is not None else derived.priority): (
            NumberSelector(
                NumberSelectorConfig(min=0, max=100, step=1, mode=NumberSelectorMode.BOX)
            )
        ),
    }
    hidden: dict[Any, Any] = {}
    for key, value in sorted(given.items()):
        if isinstance(value, bool):
            hidden[vol.Optional(f"param_{key}", default=value)] = BooleanSelector()
        elif isinstance(value, int | float):
            hidden[vol.Optional(f"param_{key}", default=float(value))] = NumberSelector(
                NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
            )
    if hidden:
        fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
    return vol.Schema(fields)


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
            new = int(edited)
        elif isinstance(value, float):
            new = float(edited)
        else:
            continue
        if new != value:
            params[key] = new
            manual.append(key)
    return params, manual


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

    # ----------------------------------------------------------------- helpers

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

    async def _labels(self) -> dict[str, str]:
        """Return the question labels in the user's language, for the review's vocabulary."""
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "config_subentries", {DOMAIN}
        )
        prefix = f"component.{DOMAIN}.config_subentries.{SUBENTRY_LOAD}.step.questions.data."
        return {
            key[len(prefix) :]: value
            for key, value in translations.items()
            if key.startswith(prefix)
        }

    def _match_schema(self, values: Mapping[str, Any] | None = None) -> vol.Schema:
        """Type, profile and the pre-bound roles, each an entity selector (§5.2)."""
        given = values or {}
        best = self._matches[0]
        chosen_profile = str(given.get("profile") or best.profile)
        match = next(m for m in self._matches if m.profile == chosen_profile)
        profile = profiles.get(chosen_profile)
        type_options = [key for key in device_types.keys() if key in profile.types] or list(  # noqa: SIM118 - `keys()` is the registry's function
            device_types.keys()
        )
        default_type = str(given.get("type") or match.suggested_type or type_options[0])
        fields: dict[Any, Any] = {
            vol.Required("type", default=default_type): SelectSelector(
                SelectSelectorConfig(
                    options=type_options,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="load_type",
                    sort=False,
                )
            ),
            vol.Required("profile", default=chosen_profile): SelectSelector(
                SelectSelectorConfig(
                    options=[m.profile for m in self._matches],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="load_profile",
                    sort=False,
                )
            ),
        }
        bound = {binding.role: binding for binding in match.bindings}
        roles = list(bound) + [role for role in match.missing if role not in bound]
        for role in roles:
            binding = bound.get(role)
            key = f"{_ROLE_PREFIX}{role.value}"
            default = given.get(key, binding.entity_id if binding is not None else None)
            required = role in match.missing or (binding is not None and binding.required)
            marker = (
                vol.Required(key, default=default)
                if required and default is not None
                else vol.Required(key)
                if required
                else vol.Optional(key, default=default)
                if default is not None
                else vol.Optional(key)
            )
            fields[marker] = EntitySelector(EntitySelectorConfig())
        return vol.Schema(fields)

    def _bindings_from_match(
        self, user_input: Mapping[str, Any]
    ) -> tuple[tuple[RoleBinding, ...], tuple[Role, ...]]:
        """Return the bindings as the profile made them, with the household's entity changes."""
        match = next(m for m in self._matches if m.profile == user_input["profile"])
        by_role = {binding.role: binding for binding in match.bindings}
        out: list[RoleBinding] = []
        missing: list[Role] = []
        wanted = set(by_role) | set(match.missing)
        for role in wanted:
            entity_id = user_input.get(f"{_ROLE_PREFIX}{role.value}")
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

    # -------------------------------------------------------------------- user

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Pick the device (any integration)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._device_id = str(user_input["device"])
            self._view = DeviceView.from_hass(self.hass, self._device_id)
            self._matches = profiles.match(self._view)
            if not self._matches:
                errors["device"] = "no_profile"
            elif any(
                subentry.data.get(LOAD_DEVICE_ID) == self._device_id
                for subentry in self._get_entry().subentries.values()
                if subentry.subentry_type == SUBENTRY_LOAD
            ):
                errors["device"] = "already_configured"
            else:
                return await self.async_step_match()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("device"): DeviceSelector()}),
            errors=errors or None,
        )

    async def async_step_match(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show what the device is: the suggested type, the profile, the roles (D4 §5.9)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        best = self._matches[0]
        if user_input is not None:
            bindings, missing = self._bindings_from_match(user_input)
            if missing:
                errors["base"] = "role_missing"
                placeholders["roles"] = ", ".join(role.value for role in missing)
            else:
                self._profile = str(user_input["profile"])
                self._type = str(user_input["type"])
                self._bindings = bindings
                return await self.async_step_questions()
        placeholders.setdefault(
            "device", load_title(self.hass, self._device_id, str(self._device_id))
        )
        placeholders.setdefault("profile", best.profile)
        placeholders.setdefault("confidence", f"{best.confidence:.0%}")
        placeholders.setdefault("reasons", "; ".join(best.reasons) or "—")
        return self.async_show_form(
            step_id="match",
            data_schema=self._match_schema(user_input),
            errors=errors or None,
            description_placeholders=placeholders,
        )

    async def async_step_questions(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask the type's questionnaire, rendered dynamically (D8 §5.4)."""
        device_type = self._device_type
        self._ctx = self._ctx or self._qctx()
        errors: dict[str, str] = {}
        values: Mapping[str, Any] | None = None
        if user_input is not None:
            raw = answers_from_form(device_type.questionnaire, user_input)
            try:
                self._answers = device_type.questionnaire.validate(raw, self._ctx)
            except AnswerError as err:
                errors[err.key] = err.code
                values = raw
            else:
                self._derived = device_type.derive(self._answers, self._ctx)
                if self.source == "reconfigure":
                    return await self.async_step_reconfigure_review()
                return await self.async_step_review()
        elif self._stored is not None:
            values = dict(self._stored.get("answers") or {})
        return self.async_show_form(
            step_id="questions",
            data_schema=question_schema(
                device_type.questionnaire, str(self._type), self._ctx, values
            ),
            errors=errors or None,
            description_placeholders={"type": str(self._type)},
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
        labels = await self._labels()
        return self.async_show_form(
            step_id="review",
            data_schema=_review_schema(
                self._derived,
                device_type,
                name=load_title(self.hass, self._device_id, str(self._type)),
            ),
            description_placeholders={
                "explanation": explanation_text(
                    self._derived, self._answers, labels, labels.get("__type__", str(self._type))
                ),
                "strategy": self._derived.strategy,
            },
            last_step=True,
        )

    def _materialise(
        self, user_input: Mapping[str, Any], params: Mapping[str, Any], manual: Sequence[str]
    ) -> dict[str, Any]:
        """Return the subentry's data (D8 §4 `LoadSubentryData`, INV-66)."""
        assert self._answers is not None
        assert self._derived is not None
        data = materialise(str(self._type), self._answers, self._derived)
        data[LOAD_PARAMS] = {key: jsonable(value) for key, value in sorted(params.items())}
        data[LOAD_STRATEGY] = str(user_input.get("strategy") or self._derived.strategy)
        data[LOAD_PRIORITY] = int(user_input.get("priority", self._derived.priority))
        data[LOAD_PROFILE] = self._profile
        data[LOAD_DEVICE_ID] = self._device_id
        data[LOAD_BINDINGS] = [binding_to_data(binding) for binding in self._bindings]
        data[LOAD_MANUAL_OVERRIDES] = list(manual)
        data["zone"] = None
        data["circuit"] = None
        return data

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
        if self._device_id:
            self._view = DeviceView.from_hass(self.hass, self._device_id)
            self._matches = profiles.match(self._view)
        return await self.async_step_questions(user_input)

    async def async_step_reconfigure_review(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Re-derive from the answers, show the diff, keep or apply (INV-66)."""
        assert self._stored is not None
        assert self._answers is not None
        assert self._derived is not None
        device_type = self._device_type
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
            data = self._materialise(user_input, params, manual)
            data[LOAD_DERIVATION_VERSION_KEY] = fresh.derivation_version
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=str(user_input["name"]),
                data=data,
            )
        labels = await self._labels()
        diff_lines = [
            f"{labels.get(key, key)}: {_pretty(old)} → {_pretty(new)}"
            for key, (old, new) in diff.items()
        ]
        subentry = self._get_reconfigure_subentry()
        schema = _review_schema(
            fresh,
            device_type,
            name=subentry.title,
            strategy=str(self._stored.get(LOAD_STRATEGY) or fresh.strategy),
            priority=int(self._stored.get(LOAD_PRIORITY, fresh.priority)),
        ).extend({vol.Optional("rederive", default=True): BooleanSelector()})
        return self.async_show_form(
            step_id="reconfigure_review",
            data_schema=schema,
            description_placeholders={
                "explanation": explanation_text(
                    fresh, self._answers, labels, labels.get("__type__", str(self._type))
                ),
                "diff": "\n".join(f"- {line}" for line in diff_lines) or "—",
                "manual": ", ".join(previous_manual) or "—",
            },
            last_step=True,
        )


LOAD_DERIVATION_VERSION_KEY = "derivation_version"
