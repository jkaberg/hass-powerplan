"""The forecaster registry - extension is by registry (D1 §6).

`same_weekday_profile` is WP4.2 and lands as one more module registered the
same way, taking its place in the middle of the chain.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..model import Schema
from .base import PriceForecaster, chain


@dataclass(frozen=True, slots=True)
class ForecasterEntry:
    """One registered forecaster (D1 §6)."""

    key: str
    schema: Schema
    factory: Callable[..., PriceForecaster]


_REGISTRY: dict[str, ForecasterEntry] = {}


def register[F: PriceForecaster](cls: type[F]) -> type[F]:
    """Register a forecaster class under its own `key` (D1 §6)."""
    _REGISTRY[cls.key] = ForecasterEntry(key=cls.key, schema=cls.schema, factory=cls)
    return cls


def keys() -> tuple[str, ...]:
    """Return every registered forecaster key, sorted."""
    return tuple(sorted(_REGISTRY))


def entry(key: str) -> ForecasterEntry:
    """Return the registry entry for `key`."""
    return _REGISTRY[key]


def build(key: str, options: Mapping[str, Any]) -> PriceForecaster:
    """Build the forecaster `key` from the options the config flow saved."""
    return _REGISTRY[key].factory(**options)


def chain_from(config: Sequence[tuple[str, Mapping[str, Any]]]) -> PriceForecaster:
    """Build the configured forecaster chain, in the configured order (D1 §5.5).

    The chain has to end in a forecaster that always succeeds, or the curve can
    fall short of the horizon and `build_curve` refuses it (INV-5).
    """
    return chain(*(build(key, options) for key, options in config))
