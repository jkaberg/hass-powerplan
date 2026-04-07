"""The v1 price forecasters and their registry (D1 §5.5, §6).

Importing this package registers every forecaster it ships: carry what is
known, estimate from the same weekday, and - always - the synthesised floor.
"""

from . import carry_known, same_weekday, synthesised  # noqa: F401
from .base import Chain, PriceForecaster, chain, missing_intervals
from .registry import ForecasterEntry, build, chain_from, entry, keys, register

__all__ = [
    "Chain",
    "ForecasterEntry",
    "PriceForecaster",
    "build",
    "chain",
    "chain_from",
    "entry",
    "keys",
    "missing_intervals",
    "register",
]
