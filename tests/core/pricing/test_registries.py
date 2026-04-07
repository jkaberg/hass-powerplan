"""Extension is by registry, not by conditional (D1 §6).

Every modifier and forecaster is one module that registers itself with a schema
the config flow can render. WP4.2's three modifiers and its forecaster were
added without touching the registry, which is what these tests are for; the
roster below is now D1 §5.4's and §5.5's in full.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal

import pytest

from custom_components.powerplan.core.pricing import forecasters, modifiers
from custom_components.powerplan.core.pricing.model import Field

D1_MODIFIERS = (
    "cumulative_tier",
    "day_type",
    "export_price",
    "fixed_price",
    "levy",
    "spot_scale",
    "subsidy_threshold",
    "tou_schedule",
    "vat",
)
D1_FORECASTERS = ("carry_known", "same_weekday_profile", "synthesised")


def test_the_registries_hold_the_whole_d1_roster() -> None:
    """D1 §5.4's nine modifiers and §5.5's three forecasters, and nothing else."""
    assert modifiers.keys() == D1_MODIFIERS
    assert forecasters.keys() == D1_FORECASTERS


@pytest.mark.parametrize("key", D1_MODIFIERS)
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


@pytest.mark.parametrize("key", D1_FORECASTERS)
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

    forecast = forecasters.chain_from(
        [("carry_known", {}), ("same_weekday_profile", {}), ("synthesised", {})]
    )
    assert [part.key for part in forecast.parts] == [
        "carry_known",
        "same_weekday_profile",
        "synthesised",
    ]


def _options(key: str) -> dict[str, object]:
    """Return the minimum options each modifier needs to be built."""
    required: dict[str, dict[str, object]] = {
        "cumulative_tier": {"tiers": ()},
        "day_type": {"rates": {}},
        "export_price": {},
        "fixed_price": {"price": Decimal("0.40")},
        "levy": {"amount": Decimal("0.0163")},
        "spot_scale": {},
        "subsidy_threshold": {"threshold": Decimal("0.9125")},
        "tou_schedule": {},
        "vat": {"rate": Decimal("0.25")},
    }
    return required[key]
