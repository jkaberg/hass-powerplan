"""Registry schemas in, `vol.Schema` out (D8 §5.4).

D1's `Field`/`FieldKind` and D4's `Question` are the same idea - a registered
extension describing its own options so the flow renders from the registry and
nothing switches on a key (D1 §6, `design/DECISIONS.md` D-0037). This module owns
the `Field` half; the `Question` half arrives with the load subentry flow in
WP2.4 and will sit beside it.

Two rules carry the money:

* a `MONEY` field, and any field whose registry default is a `Decimal`, crosses
  `entry.data` as a **decimal string**. A config entry is serialised with orjson,
  which cannot write a `Decimal` at all, and a float would turn Tensio's 0.3604
  into something that is not 0.3604 (HLD §7.2, D-0123).
* nothing is clamped and nothing is bounded below zero: a price, an offset and a
  threshold may all be negative (INV-51).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)

from custom_components.powerplan.const import SECTION_ADVANCED
from custom_components.powerplan.core.pricing import FieldKind

if TYPE_CHECKING:
    from homeassistant.helpers.selector import Selector

    from custom_components.powerplan.core.pricing import Field, Schema

__all__ = ["jsonable", "render", "store_value", "value_of"]


def jsonable(value: Any) -> Any:
    """Return `value` with every `Decimal` in it as an exact decimal string.

    A config entry is written with orjson, which refuses a `Decimal` outright, and
    a preset's own numbers arrive as `Decimal`s nested inside lists and mappings -
    a time-of-use table's prices, a tier's bands. Converting to `str` rather than
    to `float` is the whole point: 0.3604 is a price on an invoice, not a binary
    fraction (HLD §7.2, D-0123).
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _select(options: tuple[str, ...], translation_key: str | None) -> Selector[Any]:
    config = SelectSelectorConfig(
        options=list(options),
        mode=SelectSelectorMode.DROPDOWN,
        sort=False,
    )
    if translation_key is not None:
        config["translation_key"] = translation_key
    return SelectSelector(config)


def _number(field: Field) -> Selector[Any]:
    config = NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any")
    if field.unit not in (None, "fraction", "factor", "per_kwh"):
        config["unit_of_measurement"] = field.unit
    return NumberSelector(config)


#: One selector per kind that needs nothing from the field itself (D8 §5.4).
#: `SELECT` needs the options and `MONEY`/`NUMBER` the unit, so those two are not
#: in the table.
_PLAIN: Mapping[FieldKind, Callable[[], Selector[Any]]] = {
    FieldKind.BOOL: BooleanSelector,
    FieldKind.TIME: TimeSelector,
    FieldKind.LIST: ObjectSelector,
    FieldKind.ENTITY: lambda: EntitySelector(EntitySelectorConfig()),
    FieldKind.TEXT: lambda: TextSelector(TextSelectorConfig()),
}


#: What a translation key may be (hassfest's own rule): an option value outside it -
#: a market area such as `NO3` - is a code shown as itself, not a word to translate.
_TRANSLATABLE = re.compile(r"(?![_-])[a-z0-9_-]+(?<![_-])")


def _selector(field: Field, translation_key: str | None) -> Selector[Any]:
    """Return the selector one registry field renders as (D8 §5.4).

    Every option of a `SELECT` field is translated under
    `selector.<prefix>_<field>` (review HUB-10), unless its values are codes
    that no translation key can spell - a Nord Pool area is `NO3` in every
    language.
    """
    if field.kind is FieldKind.SELECT:
        options = tuple(str(option) for option in field.options)
        if not all(_TRANSLATABLE.fullmatch(option) for option in options):
            translation_key = None
        return _select(options, translation_key)
    plain = _PLAIN.get(field.kind)
    return plain() if plain is not None else _number(field)


def _is_decimal(field: Field) -> bool:
    """Return whether this field's value must survive as an exact decimal."""
    return field.kind is FieldKind.MONEY or isinstance(field.default, Decimal)


def _default(field: Field, given: Any) -> Any:
    """Return what the form shows, in the type the selector accepts."""
    value = field.default if given is None else given
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if field.kind is FieldKind.MONEY and isinstance(value, str):
        return float(value)
    return value


def marker(key: str, default: Any) -> Any:
    """Return the schema marker for one field: optional, with its default.

    Nothing the flow asks is `vol.Required`. Every answer has a default
    (HLD §7.9 (2)), and a field with no default is one the household may leave
    empty - an unbound meter role, an export sensor it does not have.
    """
    return vol.Optional(key, default=default) if default is not None else vol.Optional(key)


def advanced_section(fields: Mapping[Any, Any]) -> Any:
    """Wrap `fields` in the collapsed advanced section (INV-65, D-0129)."""
    return section(vol.Schema(dict(fields)), {"collapsed": True})


def render(
    schema: Schema,
    *,
    translation_prefix: str,
    values: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Selector[Any]] | None = None,
) -> vol.Schema:
    """Render a registry `Schema` as a form (D8 §5.4).

    An advanced field goes into a collapsed `advanced` section rather than being
    dropped: it is pre-filled, never required, and there to be found (INV-65).
    `overrides` replaces one field's selector where the registry's kind is not
    specific enough to pick one - the only case in v1 is a config entry id
    (D-0122); everything else comes from the kind.
    """
    given = values or {}
    chosen = overrides or {}
    fields: dict[Any, Any] = {}
    hidden: dict[Any, Any] = {}
    for field in schema:
        default = _default(field, given.get(field.key))
        selector = chosen.get(field.key) or _selector(
            field, f"{translation_prefix}_{field.key}" if field.options else None
        )
        (hidden if field.advanced else fields)[marker(field.key, default)] = selector
    if hidden:
        fields[vol.Optional(SECTION_ADVANCED, default={})] = advanced_section(hidden)
    return vol.Schema(fields)


def store_value(field: Field, value: Any) -> Any:
    """Return `value` in the form `entry.data` holds it (D-0123)."""
    if value is None:
        return None
    if _is_decimal(field) and not isinstance(value, (Mapping, list, tuple)):
        return str(Decimal(str(value)))
    return jsonable(value)


def value_of(schema: Schema, answers: Mapping[str, Any]) -> dict[str, Any]:
    """Return every answered option of one registry entry, ready to store."""
    stored: dict[str, Any] = {}
    for field in schema:
        if field.key in answers:
            stored[field.key] = store_value(field, answers[field.key])
        elif field.default is not None:
            stored[field.key] = store_value(field, field.default)
    return stored
