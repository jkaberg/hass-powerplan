"""The country registry - a new country is one module (D13 §5.1).

A country without a module still works: its tariff is typed by hand and its VAT
asked (D13 §9.1). Nothing switches on a country code outside a module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import CountryModule

_REGISTRY: dict[str, CountryModule] = {}


def register(module: CountryModule) -> CountryModule:
    """Register a country's module under its ISO code and return it."""
    _REGISTRY[module.code] = module
    return module


def get(code: str | None) -> CountryModule | None:
    """Return the module for `code` (`hass.config.country`), or `None`."""
    return _REGISTRY.get((code or "").upper())


def codes() -> tuple[str, ...]:
    """Return every registered country code, sorted."""
    return tuple(sorted(_REGISTRY))


def for_time_zone(zone: str | None) -> str | None:
    """Return the country whose module lists HA's time zone, for the flow's pre-selection (step 0-prime)."""
    return next(
        (code for code, module in sorted(_REGISTRY.items()) if zone in module.time_zones), None
    )
