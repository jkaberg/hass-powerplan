"""Every numeric parameter names its source (D9 §2: a value without one is a bug)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

MODULES = (
    "base",
    "slab",
    "room",
    "tank",
    "ev",
    "heatpump",
    "cycle",
    "charger_ble",
    "meter",
    "weather",
    "prices",
    "uncontrolled",
    "household",
)

#: What a source must not be.  A short answer is fine - "SI", "the calendar" -
#: but an empty one, or a promise, is not a source.
PLACEHOLDERS = frozenset({"", "-", "?", "todo", "tbd", "n/a", "see above", "unknown"})


def _numeric_constants(module: ModuleType) -> dict[str, object]:
    """Module-level UPPER_CASE numbers and numeric tuples."""
    out: dict[str, object] = {}
    for name, value in vars(module).items():
        if not name.isupper() or name.startswith("_") or name == "SOURCES":
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) or (
            isinstance(value, tuple) and value and _all_numeric(value)
        ):
            out[name] = value
    return out


def _all_numeric(value: tuple[object, ...]) -> bool:
    return all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)


@pytest.mark.parametrize("name", MODULES)
def test_every_numeric_constant_has_a_source(name: str) -> None:
    """No unsourced number reaches the benchmark's fiction."""
    module = importlib.import_module(f"tests.sim.{name}")
    sources: dict[str, str] = module.SOURCES
    missing = sorted(set(_numeric_constants(module)) - set(sources))
    assert not missing, f"tests/sim/{name}.py: no source for {missing}"


@pytest.mark.parametrize("name", MODULES)
def test_sources_name_real_constants_and_say_something(name: str) -> None:
    """A source entry points at a constant that exists and is not a placeholder."""
    module = importlib.import_module(f"tests.sim.{name}")
    sources: dict[str, str] = module.SOURCES
    for key, text in sources.items():
        assert hasattr(module, key), f"tests/sim/{name}.py: SOURCES names {key}, which is gone"
        assert text.strip(), f"tests/sim/{name}.py: {key}'s source is empty"
        assert text.strip().lower() not in PLACEHOLDERS, (
            f"tests/sim/{name}.py: {key}'s source is a placeholder"
        )
        if text.startswith("assumed"):
            # An assumption must say what would replace it, or why it needs nothing.
            assert ":" in text, f"tests/sim/{name}.py: {key} is assumed without saying what"
