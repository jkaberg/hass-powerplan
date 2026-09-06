"""The entity-format registry - extension is by registry (D1 §2, §6).

One entry per row of D1 §2's format table, and the table is asserted against this
registry by `tests/providers/prices/formats/test_registry_table.py`: a row the
design names and nothing implements fails, and so does an adapter the design does
not name.

An entry's `kind` says which protocol the adapter follows - `parse(state)` for the
rows whose prices are on an entity, `fetch(hass, day)` for the two whose prices
are behind a response action - so the prices step can build the right source
without switching on a key (`design/DECISIONS.md` D-0100).

`for_platform` is what the prices step of the config flow uses to pre-select a
format from the entity registry's `platform` (D1 §6, "read-only: detected format
name"), so the flow renders from the registry and never switches on a key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from custom_components.powerplan.core.pricing import Schema

from .base import ActionFormat, EntityFormat, FormatKind

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class FormatEntry:
    """One registered format, entity-backed or action-backed (D1 §2)."""

    key: str
    platform: str | None
    schema: Schema
    kind: FormatKind
    factory: Callable[..., EntityFormat | ActionFormat]


_REGISTRY: dict[str, FormatEntry] = {}


def register[F: EntityFormat | ActionFormat](cls: type[F]) -> type[F]:
    """Register a format class under its own `key`."""
    _REGISTRY[cls.key] = FormatEntry(
        key=cls.key,
        platform=cls.platform,
        schema=cls.schema,
        kind=cls.kind,
        factory=cls,
    )
    return cls


def keys() -> tuple[str, ...]:
    """Return every registered format key, sorted."""
    return tuple(sorted(_REGISTRY))


def entry(key: str) -> FormatEntry:
    """Return the registry entry for `key`."""
    return _REGISTRY[key]


def build(key: str, options: Mapping[str, Any] | None = None) -> EntityFormat | ActionFormat:
    """Build the format `key` from the options the config flow saved.

    Which of the two protocols comes back is `entry(key).kind`; the caller is the
    prices step, which knows it is building an `EntitySource` or an `ActionSource`
    from it (D-0100).
    """
    return _REGISTRY[key].factory(**(options or {}))


#: What a source's price includes when its adapter says nothing: spot alone (O5).
SPOT_ONLY: Final = frozenset({"spot"})


def basis(key: str) -> frozenset[str]:
    """Return what a format's prices already include ⊆ {spot, grid, vat, levies} (D1 §5.3, O5)."""
    found: frozenset[str] = getattr(_REGISTRY[key].factory, "basis", SPOT_ONLY)
    return found


def for_platform(platform: str) -> tuple[str, ...]:
    """Return the format keys that claim `platform`, sorted (D1 §6).

    More than one row can claim the same platform - `nordpool` is both the HACS
    sensor and the core integration - so the flow gets the candidates and asks
    only when it cannot tell them apart.
    """
    return tuple(sorted(key for key, found in _REGISTRY.items() if found.platform == platform))


__all__ = ["FormatEntry", "build", "entry", "for_platform", "keys", "register"]
