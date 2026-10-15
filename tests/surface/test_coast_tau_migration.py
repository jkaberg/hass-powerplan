"""Config entry minor version 4: every heating load carries its coast time constant (D-0730).

The default its type derives today, written in once (INV-66); the daily fit
replaces it where it passes its gate (INV-63). The site's own migration to
minor 4 is `test_entity_refs`'s 13b, which ends on `ENTRY_MINOR`.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.storage import (
    ENTRY_MINOR,
    ENTRY_MINOR_COAST_TAU,
    migrate_coast_tau,
)


def _load(type_key: str, **params: object) -> dict[str, object]:
    return {"type": type_key, "params": dict(params), "strategy": "heat_capacitor"}


@pytest.mark.inv("INV-66")
def test_a_slab_loop_gets_the_slabs_default() -> None:
    """The reference house's loops: cable in screed, 60 h."""
    migrated = migrate_coast_tau(_load("floor_heating", store="slab", area_m2=7.0))
    assert migrated is not None
    assert migrated["params"]["coast_tau_h"] == 60.0
    assert migrated["params"]["area_m2"] == 7.0
    assert migrated["strategy"] == "heat_capacitor"


def test_a_foil_loop_gets_the_rooms_default() -> None:
    """A foil heats the room's air: 10 h."""
    migrated = migrate_coast_tau(_load("floor_heating", store="room"))
    assert migrated is not None
    assert migrated["params"]["coast_tau_h"] == 10.0


def test_a_heat_pumps_room_is_its_mass_over_its_loss() -> None:
    """150 m³ of light-weight construction (1.8 kWh/K, D-0778) over 70 W/K: ≈ 25.7 h."""
    migrated = migrate_coast_tau(_load("heat_pump", volume_m3=150.0, heat_loss_w_per_k=70.0))
    assert migrated is not None
    assert migrated["params"]["coast_tau_h"] == 25.7


def test_a_panel_heater_waits_for_the_house() -> None:
    """No loss is known for a panel heater's room, so no time constant until the fit."""
    migrated = migrate_coast_tau(_load("radiator", volume_m3=30.0))
    assert migrated is not None
    assert migrated["params"]["coast_tau_h"] is None


def test_what_is_already_there_and_other_types_are_left_alone() -> None:
    """A load that has one keeps it; a car or a tank has no coast to plan."""
    assert migrate_coast_tau(_load("floor_heating", store="slab", coast_tau_h=59.3)) is None
    assert migrate_coast_tau(_load("ev", capacity_kwh=64.0)) is None
    assert migrate_coast_tau(_load("water_heater", litres=300.0)) is None


def test_minor_4_comes_before_where_a_new_entry_starts() -> None:
    """A new entry needs no migration; minor 4 is one of the steps an old one takes."""
    assert ENTRY_MINOR_COAST_TAU == 4
    assert ENTRY_MINOR >= ENTRY_MINOR_COAST_TAU
