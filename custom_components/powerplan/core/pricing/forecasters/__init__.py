"""The v1 price forecasters and their registry (D1 §5.5, §6).

Importing this package registers every forecaster it ships.
`same_weekday_profile` - the `ESTIMATED` middle of the chain - is WP4.2 and
lands as one more module registered the same way.
"""

from . import carry_known, synthesised  # noqa: F401
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
