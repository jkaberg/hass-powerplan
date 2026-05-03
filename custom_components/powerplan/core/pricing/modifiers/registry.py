"""The modifier registry - extension is by registry, not by conditional (D1 §6).

A registry entry carries the modifier's key, the component it writes and the
schema the price step of the config flow renders from. `build` constructs one
from the options the flow saved; `chain_from` builds the whole configured chain
in order. `day_type`, `cumulative_tier` and `export_price` are WP4.2 and land
as three more modules registered the same way.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..model import Field, FieldKind, Schema
from .base import PriceModifier


@dataclass(frozen=True, slots=True)
class ModifierEntry:
    """One registered modifier (D1 §6)."""

    key: str
    component: str
    schema: Schema
    factory: Callable[..., PriceModifier]


_REGISTRY: dict[str, ModifierEntry] = {}


def register[M: PriceModifier](cls: type[M]) -> type[M]:
    """Register a modifier class under its own `key` (D1 §6)."""
    _REGISTRY[cls.key] = ModifierEntry(
        key=cls.key,
        component=cls.component,
        schema=cls.schema,
        factory=cls,
    )
    return cls


def keys() -> tuple[str, ...]:
    """Return every registered modifier key, sorted."""
    return tuple(sorted(_REGISTRY))


def entry(key: str) -> ModifierEntry:
    """Return the registry entry for `key`."""
    return _REGISTRY[key]


def build(key: str, options: Mapping[str, Any]) -> PriceModifier:
    """Build the modifier `key` from the options the config flow saved.

    `entry.data` holds JSON: a `MONEY` field is a decimal string and a `LIST`
    field a list - of scalars, or of records for a schedule, a tier table, a
    day-type rate. The scalars are decoded here from the schema; a modifier
    whose records need typing declares `from_options` and gets the decoded
    mapping (`design/DECISIONS.md` D-0270). Typed options - a test passing
    `Decimal`s and `TouPeriod`s - pass through unchanged.
    """
    entry_ = _REGISTRY[key]
    decoded = {
        field.key: _decode(field, options[field.key])
        for field in entry_.schema
        if field.key in options
    }
    from_options = getattr(entry_.factory, "from_options", None)
    if from_options is not None:
        built: PriceModifier = from_options(decoded)
        return built
    return entry_.factory(**decoded)


def _decode(field: Field, value: Any) -> Any:
    """Return one stored option as the modifier's dataclass expects it."""
    if value is None:
        return None
    if field.kind is FieldKind.MONEY and not isinstance(value, Decimal):
        return Decimal(str(value))
    if field.kind is FieldKind.LIST and isinstance(value, list | tuple):
        return tuple(value)
    return value


def chain_from(config: Sequence[tuple[str, Mapping[str, Any]]]) -> tuple[PriceModifier, ...]:
    """Build the configured modifier chain, in the configured order (D1 §5.3)."""
    return tuple(build(key, options) for key, options in config)
