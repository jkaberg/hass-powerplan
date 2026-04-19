"""Fixtures for the device-profile tests (D4 §9 5, 6, 16; D9 §9 7).

A profile is the only place the integration learns what a real device's entities
*mean*, so these tests are driven from the committed captures in
`tests/fixtures/captured/` - the reference house's own charger, its Z-TRM floor
thermostat, its ESPHome air-to-air pump and two `generic_thermostat` helpers.
Nothing here writes a `DeviceView` by hand: a view that agrees with the code but
not with the house would prove nothing (D9 §5.8).

`dump_view` is the D9 §9 7 loader - the production `DeviceView.from_dump`, not a
test-local reimplementation. Its keyword arguments edit the *dump* rather than the
view, which is how a captured charger is walked through its nine documented
statuses without a test-only mutator on the production type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.providers.profiles import DeviceView

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from custom_components.powerplan.core.loads import Role
    from custom_components.powerplan.providers.profiles import MatchResult, RoleBinding

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "captured"
GOLDEN = Path(__file__).resolve().parents[2] / "golden" / "profiles"

#: The charger's own entity ids, as `easee_ble` names them (the ancestor's README).
LIMIT = "number.garasje_billader_dynamic_charger_current"
ENABLE = "switch.garasje_billader_charger_enabled"
STATUS = "sensor.garasje_billader_status"
POWER = "sensor.garasje_billader_power"
SESSION_ENERGY = "sensor.garasje_billader_session_energy"
BLOCKED_BY = "sensor.garasje_billader_charging_blocked_by"
PHASE_MODE = "select.garasje_billader_phase_mode"
BLUETOOTH_MODE = "select.garasje_billader_bluetooth_mode"


def load_dump(name: str) -> dict[str, Any]:
    """Return the captured dump `name` as `capture_fixture.py` wrote it."""
    document: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    return document


def dump_view(
    name: str,
    *,
    states: Mapping[str, str] | None = None,
    drop: Sequence[str] = (),
    platform: str | None = None,
) -> DeviceView:
    """Load a captured dump as a `DeviceView` - the D9 §9 7 round-trip's first leg.

    `states` moves an entity, `drop` removes one and `platform` records the owning
    integration the REST capture cannot know. All three edit the document, so what
    is under test is always the production loader.
    """
    document = load_dump(name)
    entities = [entity for entity in document["entities"] if entity["entity_id"] not in set(drop)]
    for entity in entities:
        if states and entity["entity_id"] in states:
            entity["state"] = states[entity["entity_id"]]
    document["entities"] = entities
    if platform is not None:
        document["platform"] = platform
    return DeviceView.from_dump(document)


@pytest.fixture
def easee() -> DeviceView:
    """Return the reference house's charger: 24 entities, `disconnected`, 10 A armed."""
    return dump_view("easee_ble_charger")


@pytest.fixture
def negatives() -> dict[str, DeviceView]:
    """Every captured device that is *not* an Easee charger (D4 §9 16)."""
    return {
        name: dump_view(name)
        for name in (
            "heatit_z_trm2fx_floor",
            "esphome_air_to_air_heatpump",
            "generic_thermostat_panel_heater",
            "generic_thermostat_water_heater",
        )
    }


@pytest.fixture
def golden() -> Callable[[str], dict[str, Any]]:
    """Return a loader for a golden profile-match record."""

    def load(name: str) -> dict[str, Any]:
        document: dict[str, Any] = json.loads((GOLDEN / f"{name}.json").read_text("utf-8"))
        return document

    return load


def as_record(match: MatchResult) -> dict[str, Any]:
    """Serialise a `MatchResult` the way the golden file records it.

    The shape is deliberately flat and sorted: a golden file is read by a human
    reviewing a match that changed, so it holds the entity ids and the numbers
    the flow will show, not a pickled object.
    """
    return {
        "profile": match.profile,
        "confidence": match.confidence,
        "suggested_type": match.suggested_type,
        "reasons": list(match.reasons),
        "missing": [str(role) for role in match.missing],
        "bindings": {
            str(binding.role): {
                "entity_id": binding.entity_id,
                "unit": binding.unit,
                "scale": binding.scale,
                "step": binding.step,
                "min": binding.min_value,
                "max": binding.max_value,
                "required": binding.required,
                "writable": binding.writable,
                "options": list(binding.options),
            }
            for binding in sorted(match.bindings, key=lambda found: str(found.role))
        },
    }


def binding_of(match: MatchResult, role: Role) -> RoleBinding | None:
    """Return the binding `match` made for `role`, or `None`."""
    return next((found for found in match.bindings if found.role is role), None)


def entities_of(match: MatchResult) -> Mapping[str, str]:
    """Return role → entity_id for every binding, for a one-line assertion."""
    return {str(binding.role): binding.entity_id for binding in match.bindings}
