"""Extension is by registry, not by conditional (D1 §6).

Every modifier and forecaster is one module that registers itself with a schema
the config flow can render. These tests are the contract a WP4.2 module has to
satisfy without touching the registry.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal

import pytest

from custom_components.powerplan.core.pricing import forecasters, modifiers
from custom_components.powerplan.core.pricing.model import Field

WP04_MODIFIERS = (
    "fixed_price",
    "levy",
    "spot_scale",
    "subsidy_threshold",
    "tou_schedule",
    "vat",
)
WP04_FORECASTERS = ("carry_known", "synthesised")


def test_the_registries_hold_what_wp04_ships() -> None:
    """The WP0.4 roster, and nothing invented beyond it (PLAN §3)."""
    assert modifiers.keys() == WP04_MODIFIERS
    assert forecasters.keys() == WP04_FORECASTERS


@pytest.mark.parametrize("key", WP04_MODIFIERS)
def test_every_modifier_entry_describes_its_own_options(key: str) -> None:
    """A registry entry's schema names real options of the module it registers."""
    entry = modifiers.entry(key)
    built = modifiers.build(key, _options(key))

    assert entry.key == key
    assert entry.component == built.component
    assert entry.schema
    assert all(isinstance(field, Field) for field in entry.schema)
    names = {field.name for field in fields(built)}  # type: ignore[arg-type]
    assert {field.key for field in entry.schema} <= names


@pytest.mark.parametrize("key", WP04_FORECASTERS)
def test_every_forecaster_entry_describes_its_own_options(key: str) -> None:
    """A forecaster's schema is a subset of its own fields (`tou` is wired, not asked)."""
    entry = forecasters.entry(key)
    built = forecasters.build(key, {})

    assert entry.key == key
    names = {field.name for field in fields(built)}  # type: ignore[arg-type]
    assert {field.key for field in entry.schema} <= names


def test_a_chain_is_built_from_configuration_in_order() -> None:
    """`chain_from` builds the configured order; nothing switches on a key (D1 §5.3)."""
    chain = modifiers.chain_from(
        [
            ("tou_schedule", {"fallback": Decimal("0.2292")}),
            ("levy", {"amount": Decimal("0.0163")}),
            ("vat", {"rate": Decimal("0.25")}),
        ]
    )
    assert [modifier.key for modifier in chain] == ["tou_schedule", "levy", "vat"]

    forecast = forecasters.chain_from([("carry_known", {}), ("synthesised", {})])
    assert [part.key for part in forecast.parts] == ["carry_known", "synthesised"]


def _options(key: str) -> dict[str, object]:
    """Return the minimum options each modifier needs to be built."""
    required: dict[str, dict[str, object]] = {
        "fixed_price": {"price": Decimal("0.40")},
        "levy": {"amount": Decimal("0.0163")},
        "spot_scale": {},
        "subsidy_threshold": {"threshold": Decimal("0.9125")},
        "tou_schedule": {},
        "vat": {"rate": Decimal("0.25")},
    }
    return required[key]
