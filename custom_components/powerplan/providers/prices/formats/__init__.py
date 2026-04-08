"""One adapter per row of D1 §2's entity format table.

Importing this package registers every format it ships. WP1.2 ships the two rows
the reference house and a hand-configured sensor need - `nordpool_hacs` and
`generic_list`; the other ten rows are WP4.4 and each one is a module registered
the same way.
"""

from . import generic_list, nordpool_hacs  # noqa: F401
from .base import EntityFormat, ParsedPrices
from .registry import FormatEntry, build, entry, for_platform, keys, register

__all__ = [
    "EntityFormat",
    "FormatEntry",
    "ParsedPrices",
    "build",
    "entry",
    "for_platform",
    "keys",
    "register",
]
