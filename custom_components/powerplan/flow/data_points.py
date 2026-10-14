"""The data points step (D8 §5.2, D4 §4.7, INV-76, D-0746 … D-0753).

    data_points        → each need down the ladder: found, tied, nearby, not found
    data_points_other  → one picker per need answered "Another entity"

The type, the control kind and the strategy say what the appliance reads
(`needs_of`); the profile's match says what the device has. A need the match
bound is **found**: shown back with its live value, in a collapsed section where
a change is one click away. Several candidates are a **tie**: asked as a choice
with each one's value, the best ranked first, never bound alone. With nothing on
the device, a helper fed from it or the one sensor of the role's class in its
room is a **nearby** suggestion, pre-filled but asked. Anything else is **not
found**: a required one blocks, a recommended one is asked with what it is for
and what happens without it, an optional one waits under Advanced. Nothing is
dropped silently, and nothing the household picked is replaced.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from custom_components.powerplan.const import SECTION_ADVANCED, SUBENTRY_LOAD
from custom_components.powerplan.core.loads import Role
from custom_components.powerplan.core.loads.needs import Need, Tier, needs_of
from custom_components.powerplan.core.loads.registry import CONTROL_KINDS, device_types
from custom_components.powerplan.core.strategies import base as strategies
from custom_components.powerplan.flow.questionnaire import advanced_section
from custom_components.powerplan.flow.text import entity_name
from custom_components.powerplan.providers.profiles.base import RoleBinding
from custom_components.powerplan.providers.profiles.candidates import ROLE_CLASS, in_pool

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.selector import EntityWithDeviceFilterSelectorConfig

    from custom_components.powerplan.flow.text import Text
    from custom_components.powerplan.providers.profiles.base import DeviceView, MatchResult

__all__ = [
    "OTHER",
    "Outcome",
    "Row",
    "Source",
    "description",
    "field_key",
    "needs_for",
    "other_schema",
    "resolve",
    "role_label",
    "schema",
    "unmet",
]

#: The tie option that opens `data_points_other`.
OTHER: Final = "other"
#: The collapsed section the found data points are shown in.
SECTION_FOUND: Final = "found"
_PREFIX: Final = "role_"


class Outcome(StrEnum):
    """Where a need landed on the ladder (D8 §5.2)."""

    FOUND = "found"
    TIED = "tied"
    NEARBY = "nearby"
    MISSING = "missing"


class Source(StrEnum):
    """Where a stored binding came from (D-0753)."""

    FOUND = "found"
    PICKED = "picked"
    NEARBY = "nearby"
    MIGRATED = "migrated"


@dataclass(frozen=True, slots=True)
class Row:
    """One need, resolved: what the step shows for it."""

    role: Role
    tier: Tier
    #: The need's text key; `None` for a role only the profile binds.
    key: str | None
    outcome: Outcome
    binding: RoleBinding | None = None
    candidates: tuple[str, ...] = ()
    suggestion: str | None = None
    disabled: tuple[str, ...] = ()

    @property
    def asked(self) -> bool:
        """Whether the row is a question in the form rather than a line."""
        return self.outcome is not Outcome.FOUND and (
            self.tier is not Tier.OPTIONAL or self.outcome is not Outcome.MISSING
        )


def field_key(role: Role) -> str:
    """Return the form field of `role`'s picker."""
    return f"{_PREFIX}{role.value}"


def needs_for(type_key: str, kind: str | None, strategy: str | None = None) -> tuple[Need, ...]:
    """Return what a load of `type_key` on `kind` with `strategy` reads (D4 §4.7)."""
    device_type = device_types.get(type_key)
    chosen_kind = kind if kind in device_type.kinds else device_type.kinds[0]
    chosen = strategies.get(strategy or device_type.default_strategy)
    return needs_of(
        device_type.needs(chosen_kind), CONTROL_KINDS[chosen_kind].kind.needs, chosen.needs
    )


def resolve(
    needs: Sequence[Need],
    match: MatchResult,
    view: DeviceView,
    *,
    near: Callable[[Role], str | None],
    stored: Mapping[Role, RoleBinding] | None = None,
) -> tuple[Row, ...]:
    """Put each need on the ladder; the profile's other roles follow as found.

    A stored binding is found whatever detection says now (D-0753). A role the
    profile requires is required here too, whatever tier its need has.
    """
    bound = match.by_role()
    held = dict(stored or {})
    tiers: dict[Role, tuple[Tier, str | None]] = {
        each.role: (each.tier, each.key) for each in needs
    }
    for role, binding in bound.items():
        if binding.required:
            tiers[role] = (Tier.REQUIRED, tiers.get(role, (Tier.REQUIRED, None))[1])
        else:
            tiers.setdefault(role, (Tier.OPTIONAL, None))
    for role in match.missing:
        tiers[role] = (Tier.REQUIRED, tiers.get(role, (Tier.REQUIRED, None))[1])
    for role in held:
        tiers.setdefault(role, (Tier.OPTIONAL, None))
    rows: list[Row] = []
    for role, (tier, key) in tiers.items():
        disabled = _disabled(view, role)
        found = held.get(role) or bound.get(role)
        if found is not None:
            rows.append(Row(role, tier, key, Outcome.FOUND, binding=found, disabled=disabled))
        elif role in match.ties:
            rows.append(
                Row(role, tier, key, Outcome.TIED, candidates=match.ties[role], disabled=disabled)
            )
        elif tier is not Tier.OPTIONAL and (suggestion := near(role)) is not None:
            rows.append(
                Row(role, tier, key, Outcome.NEARBY, suggestion=suggestion, disabled=disabled)
            )
        else:
            rows.append(Row(role, tier, key, Outcome.MISSING, disabled=disabled))
    return tuple(rows)


def unmet(
    needs: Sequence[Need],
    bound: Collection[Role],
    declined: Collection[str],
    match: MatchResult,
) -> tuple[Role, ...]:
    """Return the recommended needs a load lacks that its device has now (D-0753).

    Unbound, not declined, and found or tied on the device: an entity enabled,
    a device that gained one, a plan that reads one the old did not. Nothing is
    bound here; the household confirms it in the data points step.
    """
    have = {binding.role for binding in match.bindings} | set(match.ties)
    return tuple(
        each.role
        for each in needs
        if each.tier is Tier.RECOMMENDED
        and each.role not in bound
        and each.role.value not in declined
        and each.role in have
    )


def _disabled(view: DeviceView, role: Role) -> tuple[str, ...]:
    """Return the device's disabled sensors of the role's class, to name them."""
    device_class = ROLE_CLASS.get(role)
    if device_class is None:
        return ()
    # Only what would be a candidate once enabled: never our own, never a diagnostic.
    return tuple(
        entity.entity_id
        for entity in view.disabled
        if entity.domain == "sensor"
        and entity.device_class == device_class
        and in_pool(replace(entity, disabled=False))
    )


# --------------------------------------------------------------------------- #
# The form
# --------------------------------------------------------------------------- #


def schema(
    rows: Sequence[Row],
    filters: Mapping[Role, list[EntityWithDeviceFilterSelectorConfig]],
    option_label: Callable[[str], str],
    other_label: str,
    given: Mapping[str, Any] | None = None,
) -> vol.Schema:
    """Return the form: asked rows open, found ones collapsed, optional misses in Advanced."""
    values = dict(given or {})
    asked: dict[Any, Any] = {}
    found: dict[Any, Any] = {}
    advanced: dict[Any, Any] = {}
    for row in rows:
        key = field_key(row.role)
        picker = EntitySelector(EntitySelectorConfig(filter=filters.get(row.role, [{}])))
        if row.outcome is Outcome.FOUND:
            assert row.binding is not None
            # A suggestion, not a default: a found data point the household
            # clears stays cleared, where a default would come back on submit.
            found[_marker(row, key, values.get(key, row.binding.entity_id))] = picker
        elif row.outcome is Outcome.TIED:
            options = [
                SelectOptionDict(value=entity_id, label=option_label(entity_id))
                for entity_id in row.candidates
            ]
            options.append(SelectOptionDict(value=OTHER, label=other_label))
            asked[vol.Required(key, default=values.get(key, row.candidates[0]))] = SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST, sort=False)
            )
        elif row.outcome is Outcome.NEARBY:
            asked[_marker(row, key, values.get(key, row.suggestion))] = picker
        elif row.tier is Tier.OPTIONAL:
            advanced[_marker(row, key, values.get(key))] = picker
        else:
            asked[_marker(row, key, values.get(key))] = picker
    fields = dict(asked)
    if found:
        fields[vol.Optional(SECTION_FOUND)] = advanced_section(found)
    if advanced:
        fields[vol.Optional(SECTION_ADVANCED)] = advanced_section(advanced)
    return vol.Schema(fields)


def _marker(row: Row, key: str, default: Any) -> Any:
    """Return a required row's marker as required; a default only where there is one."""
    if row.tier is Tier.REQUIRED:
        return vol.Required(key, default=default) if default else vol.Required(key)
    return (
        vol.Optional(key, description={"suggested_value": default})
        if default
        else (vol.Optional(key))
    )


def other_schema(
    roles: Sequence[Role], filters: Mapping[Role, list[EntityWithDeviceFilterSelectorConfig]]
) -> vol.Schema:
    """One picker over the whole house per need answered "Another entity"."""
    return vol.Schema(
        {
            vol.Required(field_key(role)): EntitySelector(
                EntitySelectorConfig(filter=filters.get(role, [{}]))
            )
            for role in roles
        }
    )


# --------------------------------------------------------------------------- #
# The words
# --------------------------------------------------------------------------- #


def role_label(text: Text, role: Role) -> str:
    """Return a role's name as the step's field shows it."""
    return text.string(
        f"config_subentries.{SUBENTRY_LOAD}.step.data_points.data.{field_key(role)}"
    ) or role.value.replace("_", " ")


def reading(
    text: Text,
    view: DeviceView | None,
    entity_id: str,
    attribute: str | None,
    scale: float = 1.0,
) -> str | None:
    """Return an entity's live number with its unit; `None` for a word or nothing.

    A state word ("charging") is the integration's, in its language, not the
    household's: the step names the entity and leaves the word out.
    """
    entity = None if view is None else view.get(entity_id)
    if entity is None:
        return None
    if attribute is not None:
        number = entity.attribute_number(attribute)
        unit = entity.attribute("temperature_unit") or ("°C" if "temp" in attribute else None)
    else:
        number = entity.number
        unit = entity.unit
    if number is None:
        return None
    if scale != 1.0 and unit and unit[0].isdigit():
        # A unit that carries its own factor ("0.1 °C") reads as the binding scales it.
        number, unit = number * scale, unit.split(" ", 1)[-1]
    return f"{text.number(number)} {unit or ''}".strip()


def option_label(hass: HomeAssistant, text: Text, view: DeviceView | None, entity_id: str) -> str:
    """Return a tie's option: the entity's name and its live value."""
    name = entity_name(hass, entity_id)
    value = reading(text, view, entity_id, None)
    return name if value is None else text.word("load_text", "need_option", name=name, value=value)


def description(
    hass: HomeAssistant,
    text: Text,
    rows: Sequence[Row],
    view: DeviceView | None,
) -> dict[str, str]:
    """Return the step's `found`, `asked` and `disabled` placeholders as markdown lists."""
    found: list[str] = []
    asked: list[str] = []
    disabled: list[str] = []
    for row in rows:
        label = role_label(text, row.role)
        if row.outcome is Outcome.FOUND and row.binding is not None:
            entity = entity_name(hass, row.binding.entity_id)
            value = reading(
                text, view, row.binding.entity_id, row.binding.attribute, row.binding.scale
            )
            found.append(
                "- "
                + (
                    text.word("load_text", "need_found_plain", role=label, entity=entity)
                    if value is None
                    else text.word(
                        "load_text", "need_found", role=label, entity=entity, value=value
                    )
                )
            )
        elif row.asked and row.key is not None:
            line = text.word(
                "load_text",
                "need_asked",
                role=label,
                purpose=text.word("need_purpose", row.key),
                without=text.word("need_without", row.key),
            )
            if row.outcome is Outcome.NEARBY:
                line = f"{line} {text.word('load_text', 'need_nearby')}"
            asked.append(f"- {line}")
        disabled.extend(
            "- "
            + text.word(
                "load_text", "need_disabled", role=label, entity=entity_name(hass, entity_id)
            )
            for entity_id in row.disabled
        )
    found_title = text.word("load_text", "need_found_title")
    asked_title = text.word("load_text", "need_asked_title")
    return {
        "found": "\n".join([found_title, *found]) if found else "",
        "asked": "\n".join([asked_title, *(asked or [text.word("load_text", "need_none_asked")])]),
        "disabled": "\n".join(disabled),
    }


# --------------------------------------------------------------------------- #
# The answers
# --------------------------------------------------------------------------- #


def flat(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the form's answers with the found and Advanced sections opened."""
    out = {k: v for k, v in user_input.items() if k not in (SECTION_FOUND, SECTION_ADVANCED)}
    for section_key in (SECTION_FOUND, SECTION_ADVANCED):
        out.update(user_input.get(section_key) or {})
    return out


@dataclass(frozen=True, slots=True)
class Answer:
    """What the household said about the rows."""

    #: The entity each answered role is bound to, and where it came from.
    chosen: Mapping[Role, tuple[str, Source]]
    #: Required rows left empty.
    missing: tuple[Role, ...]
    #: Recommended rows left empty (D-0753).
    declined: tuple[Role, ...]
    #: Tied rows answered "Another entity".
    other: tuple[Role, ...]


def answer(
    rows: Sequence[Row],
    user_input: Mapping[str, Any],
    stored_sources: Mapping[Role, Source] | None = None,
) -> Answer:
    """Read the form back into choices, misses, declines and "Another entity"."""
    given = flat(user_input)
    sources = dict(stored_sources or {})
    chosen: dict[Role, tuple[str, Source]] = {}
    missing: list[Role] = []
    declined: list[Role] = []
    other: list[Role] = []
    for row in rows:
        entity_id = given.get(field_key(row.role))
        if entity_id == OTHER:
            other.append(row.role)
            continue
        if not entity_id:
            if row.tier is Tier.REQUIRED:
                missing.append(row.role)
            elif row.tier is Tier.RECOMMENDED:
                declined.append(row.role)
            continue
        chosen[row.role] = (str(entity_id), _source(row, str(entity_id), sources.get(row.role)))
    return Answer(chosen, tuple(missing), tuple(declined), tuple(other))


def _source(row: Row, entity_id: str, stored: Source | None) -> Source:
    """Return found for a kept detection, nearby for a kept suggestion, else picked."""
    if row.outcome is Outcome.FOUND and row.binding is not None:
        if row.binding.entity_id == entity_id:
            return stored or Source.FOUND
        return Source.PICKED
    if row.outcome is Outcome.NEARBY and row.suggestion == entity_id:
        return Source.NEARBY
    return Source.PICKED
