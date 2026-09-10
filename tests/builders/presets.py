"""Company tariff files kept for tests only (D13 §12.1, INV-70).

The integration ships rule templates and no company's prices; the tables WP4.6
read by hand - Tensio TS and TN, Elvia, the eight Fluvius areas, Ellevio - live
here as the expectations the adapters reproduce and as the reference house's
tariff, next to the benchmark's synthetic 2027 version.
"""

from __future__ import annotations

import copy
import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from custom_components.powerplan.core.tariffs.rules import loader

if TYPE_CHECKING:
    from custom_components.powerplan.core.tariffs import TariffSpec

__all__ = ["FIXTURE_PRESETS", "fixture_preset", "fixture_raw"]

FIXTURE_PRESETS = Path(__file__).parents[1] / "fixtures" / "presets"


@cache
def _raw(name: str) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads((FIXTURE_PRESETS / f"{name}.json").read_text(encoding="utf-8"))
    return raw


def fixture_raw(name: str) -> dict[str, Any]:
    """Return a fixture file's JSON, validated as a shipped file was (D2 §2)."""
    raw = copy.deepcopy(_raw(name))
    loader.validate(raw, source=f"fixture {name}", shipped="-2027" not in name)
    return raw


def fixture_preset(name: str) -> TariffSpec:
    """Return a fixture file as a spec, e.g. `no/tensio-ts` or `no/tensio-ts-2027`."""
    return loader.from_raw(fixture_raw(name), source=f"fixture {name}")
