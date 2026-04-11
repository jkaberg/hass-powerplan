"""One adapter per row of D1 §2's entity format table.

Importing this package registers every format it ships, which is why every row is
imported here and nothing else imports them: the registry is populated by import,
and `tests/providers/prices/formats/test_registry_table.py` asserts the roster
against D1 §2's table itself.
"""

from . import (  # noqa: F401
    amber,
    comed,
    energidataservice,
    energyzero_action,
    entsoe,
    generic_list,
    hourly_attributes,
    nordpool_core,
    nordpool_hacs,
    octopus_energy,
    pvpc,
    tge,
    tibber_action,
)
from .base import ActionFormat, EntityFormat, FormatKind, ParsedPrices
from .registry import FormatEntry, build, entry, for_platform, keys, register

__all__ = [
    "ActionFormat",
    "EntityFormat",
    "FormatEntry",
    "FormatKind",
    "ParsedPrices",
    "build",
    "entry",
    "for_platform",
    "keys",
    "register",
]
