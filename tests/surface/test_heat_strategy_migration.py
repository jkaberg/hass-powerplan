"""D5 §9 35 - config entry minor version 5 (D-0728, D-0729, D-0738).

Every load keeps its strategy: `best_save` is still offered for heating. A load
on `heat_capacitor` gets the `bank_k` its lever allows, written in once
(INV-66), and a radiator's unread `threshold_pct` becomes `min_saving`.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.powerplan.storage import (
    ENTRY_MINOR,
    ENTRY_MINOR_HEAT_PAIRS,
    migrate_heat_strategy,
)


def _load(type_key: str, strategy: str, kind: str, **strategy_params: Any) -> dict[str, Any]:
    return {
        "type": type_key,
        "strategy": strategy,
        "params": {"kind": kind},
        "strategy_params": dict(strategy_params),
    }


@pytest.mark.inv("INV-66")
def test_35_a_radiator_on_best_save_keeps_it_and_its_threshold_is_read() -> None:
    """Still `best_save`; the 10 % it was derived with is now the 0.10 the strategy reads."""
    migrated = migrate_heat_strategy(
        _load("radiator", "best_save", "switch", threshold_pct=10.0, max_off_min=120.0)
    )
    assert migrated is not None
    assert migrated["strategy"] == "best_save"
    assert migrated["strategy_params"]["min_saving"] == pytest.approx(0.10)
    assert "threshold_pct" not in migrated["strategy_params"]
    assert migrated["strategy_params"]["max_off_min"] == 120.0


@pytest.mark.parametrize(
    ("type_key", "kind", "params", "bank_k"),
    [
        ("floor_heating", "mode", {"swing_k": 1.0}, 0.0),
        ("floor_heating", "setpoint", {"swing_k": 1.5}, 1.0),
        ("radiator", "switch", {}, 0.0),
        ("radiator", "setpoint", {}, 1.0),
        ("heat_pump", "setpoint", {"band_k": 1.5}, 1.5),
    ],
)
def test_35_a_load_on_heat_capacitor_gets_the_bank_its_lever_allows(
    type_key: str, kind: str, params: dict[str, float], bank_k: float
) -> None:
    """0 where it cannot hold above comfort, the band for a heat pump, v1's 1 K else (D-0728)."""
    migrated = migrate_heat_strategy(_load(type_key, "heat_capacitor", kind, **params))
    assert migrated is not None
    assert migrated["strategy"] == "heat_capacitor"
    assert migrated["strategy_params"]["bank_k"] == pytest.approx(bank_k)
    if type_key == "heat_pump":
        assert migrated["strategy_params"]["coast_k"] == pytest.approx(bank_k)


def test_35_what_is_complete_and_what_is_not_heating_is_left_alone() -> None:
    """A load that has its `bank_k`, a generic switch, a car: nothing to do."""
    assert (
        migrate_heat_strategy(_load("floor_heating", "heat_capacitor", "mode", bank_k=0.0)) is None
    )
    assert migrate_heat_strategy(_load("generic_switch", "cheapest_hours", "switch")) is None
    assert migrate_heat_strategy(_load("ev", "deadline_fill", "current")) is None
    assert migrate_heat_strategy(_load("floor_heating", "best_save", "mode")) is None


def test_35_minor_5_is_where_a_new_entry_starts() -> None:
    """A new entry needs no migration."""
    assert ENTRY_MINOR == ENTRY_MINOR_HEAT_PAIRS == 5
