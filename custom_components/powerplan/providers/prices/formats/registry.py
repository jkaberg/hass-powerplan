"""The entity-format registry - extension is by registry (D1 §2, §6).

One entry per row of D1 §2's format table. WP1.2 registers the two rows it
implements; `energidataservice`, `entsoe`, `tibber_action`, `energyzero_action`,
`octopus_energy`, `amber`, `pvpc`, `comed`, `tge` and `hourly_attributes` are
WP4.4 and land as ten more modules, each `@register`ed, with nothing here to
change.

`for_platform` is what the prices step of the config flow uses to pre-select a
format from the entity registry's `platform` (D1 §6, "read-only: detected format
name"), so the flow renders from the registry and never switches on a key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from custom_components.powerplan.core.pricing import Schema

from .base import EntityFormat

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class FormatEntry:
    """One registered entity format (D1 §2)."""

    key: str
    platform: str | None
    schema: Schema
    factory: Callable[..., EntityFormat]


_REGISTRY: dict[str, FormatEntry] = {}


def register[F: EntityFormat](cls: type[F]) -> type[F]:
    """Register a format class under its own `key`."""
    _REGISTRY[cls.key] = FormatEntry(
        key=cls.key,
        platform=cls.platform,
        schema=cls.schema,
        factory=cls,
    )
    return cls


def keys() -> tuple[str, ...]:
    """Return every registered format key, sorted."""
    return tuple(sorted(_REGISTRY))


def entry(key: str) -> FormatEntry:
    """Return the registry entry for `key`."""
    return _REGISTRY[key]


def build(key: str, options: Mapping[str, Any] | None = None) -> EntityFormat:
    """Build the format `key` from the options the config flow saved."""
    return _REGISTRY[key].factory(**(options or {}))


def for_platform(platform: str) -> tuple[str, ...]:
    """Return the format keys that claim `platform`, sorted (D1 §6).

    More than one row can claim the same platform - `nordpool` is both the HACS
    sensor and the core integration - so the flow gets the candidates and asks
    only when it cannot tell them apart.
    """
    return tuple(sorted(key for key, found in _REGISTRY.items() if found.platform == platform))


__all__ = ["FormatEntry", "build", "entry", "for_platform", "keys", "register"]
