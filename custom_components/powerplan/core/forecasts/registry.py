"""The forecast-source registry (D10 §3, §6).

Extension is by registry, not by conditional: a new source is one module that
registers itself, and the site flow renders its options from the entry's `Schema`
(D10 §6 - the flow asks the household nothing about forecasts and the review step
says what was found).

It ships **empty**. Both v1 sources are Home Assistant adapters - `weather_entity`
over `weather.get_forecasts`, `recorder_baseline` over the recorder's statistics -
and they register when `providers/forecasts/` lands, the same way
`providers/prices/formats/` registers into D1's format table. The protocol lives in
`model.py` so this module, and D6's and D7's types, never import `providers/`
(INV-2 keeps the direction one-way).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..pricing.model import Schema
from .model import ForecastKind, ForecastSource

__all__ = ["ForecastSourceEntry", "build", "entry", "keys", "keys_for", "register"]


@dataclass(frozen=True, slots=True)
class ForecastSourceEntry:
    """One registered forecast source (D10 §6)."""

    key: str
    kind: ForecastKind
    schema: Schema
    factory: Callable[..., ForecastSource]


_REGISTRY: dict[str, ForecastSourceEntry] = {}


def register[S: ForecastSource](cls: type[S]) -> type[S]:
    """Register a forecast source class under its own `key` (D10 §6)."""
    _REGISTRY[cls.key] = ForecastSourceEntry(
        key=cls.key, kind=cls.kind, schema=cls.schema, factory=cls
    )
    return cls


def keys() -> tuple[str, ...]:
    """Return every registered source key, sorted."""
    return tuple(sorted(_REGISTRY))


def keys_for(kind: ForecastKind) -> tuple[str, ...]:
    """Return the registered keys of one kind, sorted (D10 §6's review step)."""
    return tuple(sorted(key for key, found in _REGISTRY.items() if found.kind is kind))


def entry(key: str) -> ForecastSourceEntry:
    """Return the registry entry for `key`, or raise `KeyError`."""
    return _REGISTRY[key]


def build(key: str, options: Mapping[str, Any]) -> ForecastSource:
    """Build the source `key` from the options the config flow saved."""
    return _REGISTRY[key].factory(**options)
