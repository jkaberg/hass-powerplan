"""Tariff presets: data files and the loader that validates them (D2 §3, §6).

The files are the extension point - a new DSO is a JSON file under `<cc>/`, added
by anyone, validated in CI, with a golden test per preset (D9 §3). WP0.3 ships the
Norwegian set and `custom`; the other markets in D2 §3's list are WP4.3 and the
schema and loader take them unchanged.
"""

from .loader import (
    EnergyRate,
    PresetError,
    SummaryBand,
    TariffSummary,
    load,
    summarize,
    validate,
)

__all__ = [
    "EnergyRate",
    "PresetError",
    "SummaryBand",
    "TariffSummary",
    "load",
    "summarize",
    "validate",
]
