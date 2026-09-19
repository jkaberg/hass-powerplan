"""The v1 price modifiers and their registry (D1 §5.4, §6).

Importing this package registers every modifier it ships - all nine of D1 §5.4
after WP4.2, which added three without the registry changing. A URDB rate's
12×24 matrices are the `openei_urdb` source's (D13 §12), not a modifier.
"""

from . import (  # noqa: F401
    cumulative_tier,
    day_type,
    export_price,
    fixed_price,
    levy,
    spot_scale,
    subsidy_threshold,
    tou_schedule,
    vat,
)
from .base import GRID_ENERGY, SPOT, PriceModifier, with_component
from .registry import (
    ModifierEntry,
    build,
    chain_from,
    decode_options,
    entry,
    keys,
    register,
)

__all__ = [
    "GRID_ENERGY",
    "SPOT",
    "ModifierEntry",
    "PriceModifier",
    "build",
    "chain_from",
    "decode_options",
    "entry",
    "keys",
    "register",
    "with_component",
]
