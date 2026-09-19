"""One adapter per row of D1 §2's entity format table.

Importing this package registers every format it ships, which is why every row is
imported here and nothing else imports them: the registry is populated by import,
and `tests/providers/prices/formats/test_registry_table.py` asserts the roster
against D1 §2's table itself.
"""

from . import (  # noqa: F401
    amber,
    comed,
    cz_energy_spot_prices,
    energidataservice,
    energyzero_action,
    entsoe,
    epex_spot,
    frank_energie,
    generic_list,
    hourly_attributes,
    nordpool_core,
    nordpool_hacs,
    octopus_energy,
    pvpc,
    stromligning,
    tge,
    tibber_action,
    tibber_prices,
    zonneplan_one,
)
from .base import (
    ActionFormat,
    EntityFacts,
    EntityFormat,
    FormatKind,
    ParsedPrices,
    symbol_unit,
    unit_currency,
)
from .registry import (
    SPOT_ONLY,
    FormatEntry,
    basis,
    build,
    derived,
    entry,
    for_platform,
    keys,
    register,
    tomorrow_entity,
)

__all__ = [
    "SPOT_ONLY",
    "ActionFormat",
    "EntityFacts",
    "EntityFormat",
    "FormatEntry",
    "FormatKind",
    "ParsedPrices",
    "basis",
    "build",
    "derived",
    "entry",
    "for_platform",
    "keys",
    "register",
    "symbol_unit",
    "tomorrow_entity",
    "unit_currency",
]
