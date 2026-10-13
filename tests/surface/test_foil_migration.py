"""D4 §9 54 - a foil floor measured as a foil, and config entry minor version 7 (D-0740).

The reference house's three foils under laminate were derived at the mat table's
120 W/m² (1 200, 3 600 and 1 920 W) and drew 39–57 W/m²; TV-stua's coast was fitted
at 383 h while the heat pumps held its room. A new foil under a floating floor
derives 60 W/m²; minor 7 corrects what the old derivation materialised and leaves
what a household typed.
"""

from __future__ import annotations

import pytest

from custom_components.powerplan.core.loads.questionnaire import QCtx
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.storage import ENTRY_MINOR, ENTRY_MINOR_FOIL, migrate_foil_floor


def _foil(**params: object) -> dict[str, object]:
    base: dict[str, object] = {
        "heating_type": "foil_or_mat",
        "covering": "laminate",
        "store": "room",
        "area_m2": 16.0,
        "w_per_m2": 120.0,
        "nameplate_w": 1920.0,
        "coast_tau_h": 382.7,
    }
    base.update(params)
    return {"type": "floor_heating", "params": base, "manual_overrides": ["comfort_c"]}


@pytest.mark.inv("INV-66")
def test_54_tv_stua_gets_the_foils_rating_and_the_default_coast() -> None:
    """1 920 W → 960 W, 382.7 h → 10 h; everything else untouched."""
    migrated = migrate_foil_floor(_foil())
    assert migrated is not None
    params = migrated["params"]
    assert params["nameplate_w"] == 960.0
    assert params["w_per_m2"] == 60.0
    assert params["coast_tau_h"] == 10.0
    assert params["area_m2"] == 16.0
    assert migrated["manual_overrides"] == ["comfort_c"]


def test_54b_a_measured_rating_and_a_mat_under_tile_are_left() -> None:
    """A rating the recorder gave is not the table's; a mat under tile is 120 W/m²."""
    assert migrate_foil_floor(_foil(nameplate_w=620.0, coast_tau_h=10.0)) is None
    assert migrate_foil_floor(_foil(covering="tile", coast_tau_h=10.0)) is None


def test_54c_a_coast_the_household_typed_stays() -> None:
    """`coast_tau_h` among the manual overrides is the household's number."""
    data = _foil(nameplate_w=620.0)
    data["manual_overrides"] = ["coast_tau_h"]
    assert migrate_foil_floor(data) is None


def test_54d_slabs_and_other_types_are_left() -> None:
    """A cable in screed coasts for days; a tank has no floor."""
    slab = {
        "type": "floor_heating",
        "params": {"heating_type": "cable_in_screed", "coast_tau_h": 117.4},
    }
    assert migrate_foil_floor(slab) is None
    assert migrate_foil_floor({"type": "water_heater", "params": {"litres": 300.0}}) is None


def test_54e_a_new_foil_under_laminate_derives_60_w_per_m2() -> None:
    """The derivation: 16 m² of foil under laminate is 960 W, under tile 1 920 W."""
    floor = device_types.get("floor_heating")
    for covering, watts in (("laminate", 960.0), ("tile", 1920.0)):
        raw = {
            "room": "living",
            "covering": covering,
            "heating_type": "foil_or_mat",
            "area_m2": 16.0,
        }
        derived = floor.derive(floor.questionnaire.validate(raw, QCtx()), QCtx())
        assert derived.params["nameplate_w"] == watts


def test_54f_minor_7_comes_before_where_a_new_entry_starts() -> None:
    """A new entry needs no migration; minor 7 is one of the steps an old one takes."""
    assert ENTRY_MINOR_FOIL == 7
    assert ENTRY_MINOR >= ENTRY_MINOR_FOIL
