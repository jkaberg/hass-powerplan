"""Entity references in stored config: registry ids at rest, entity ids in use (D8 §4).

What a flow saves names an entity by its entity-registry id - the UUID that
survives a household renaming the entity id (#13, D-0726). Everything that
reads the config reads entity ids, resolved here, the way Home Assistant's own
`er.async_resolve_entity_id` does. An entity the registry does not know (a YAML
sensor with no unique id) has no registry id and stays an entity id; a device id
has the same shape as a registry id but is never one, so it resolves to itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.core import valid_entity_id
from homeassistant.helpers import entity_registry as er

from .const import CONF_NOTIFICATIONS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

#: Keys whose values are never entity references: a notify service is spelled
#: like an entity id, and a notify entity of the same name would capture it.
_NOT_ENTITIES = frozenset({CONF_NOTIFICATIONS})


def stored(hass: HomeAssistant, value: Any) -> Any:
    """Return `value` with every registered entity id replaced by its registry id.

    Idempotent: a registry id is not an entity id and passes through, so a write
    that spreads already stored data over new answers can run it again.
    """
    registry = er.async_get(hass)

    def convert(text: str) -> str:
        entry = registry.async_get(text) if valid_entity_id(text) else None
        return text if entry is None else entry.id

    return _walk(value, convert)


def live(hass: HomeAssistant, value: Any) -> Any:
    """Return `value` with every registry id replaced by the entity id it has now."""
    registry = er.async_get(hass)

    def convert(text: str) -> str:
        return er.async_resolve_entity_id(registry, text) or text

    return _walk(value, convert)


def referenced(entry: ConfigEntry) -> frozenset[str]:
    """Return every string the site and its subentries store - the candidates for a registry id."""
    found: set[str] = set()

    def collect(text: str) -> str:
        found.add(text)
        return text

    _walk(entry.data, collect)
    for subentry in entry.subentries.values():
        _walk(subentry.data, collect)
    return frozenset(found)


def _walk(value: Any, convert: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return convert(value)
    if isinstance(value, Mapping):
        return {
            key: item if key in _NOT_ENTITIES else _walk(item, convert)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_walk(item, convert) for item in value]
    return value
