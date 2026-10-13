"""D4 §9 52 - a lever that can't bank derives `bank_k` 0 (D-0728, D-0729, D-0738).

`heat_capacitor` is the default for every heating type, and `best_save` is still
offered. What the derivation decides is how far above comfort the plan may
warm the store: nothing where the lever can't hold it there.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.powerplan.core.loads.questionnaire import QCtx
from custom_components.powerplan.core.loads.types import base as device_types
from custom_components.powerplan.core.loads.types.radiator import BANK_ABOVE_COMFORT_K


def _derive(
    type_key: str, raw: dict[str, Any], *, capabilities: frozenset[str] = frozenset()
) -> Any:
    device_type = device_types.get(type_key)
    ctx = QCtx(capabilities=capabilities)
    return device_type.derive(device_type.questionnaire.validate(raw, ctx), ctx)


@pytest.mark.parametrize(
    ("capabilities", "bank_k"),
    [(frozenset({"mode_select"}), 0.0), (frozenset(), 1.0)],
)
def test_52_a_floor_banks_only_on_a_setpoint(capabilities: frozenset[str], bank_k: float) -> None:
    """A `mode` thermostat's comfort option is its own setpoint: it pauses only."""
    for heating in ("cable_in_screed", "foil_or_mat"):
        derived = _derive("floor_heating", {"heating_type": heating}, capabilities=capabilities)
        assert derived.strategy == "heat_capacitor"
        assert derived.strategy_params["bank_k"] == bank_k


@pytest.mark.parametrize(
    ("control", "bank_k"), [("plug", 0.0), ("thermostat", BANK_ABOVE_COMFORT_K)]
)
def test_52_a_radiator_on_a_plug_pauses_and_reads_its_threshold(
    control: str, bank_k: float
) -> None:
    """The plug leaves the dial at comfort; the 10 % is `min_saving`, not an unread key (D-0729)."""
    derived = _derive("radiator", {"control": control})
    assert derived.strategy == "heat_capacitor"
    assert derived.strategy_params["bank_k"] == bank_k
    assert derived.strategy_params["min_saving"] == pytest.approx(0.10)
    assert "threshold_pct" not in derived.strategy_params


def test_52_a_heat_pump_moves_inside_its_band() -> None:
    """`bank_k` and `coast_k` are the band the kind clamps to (INV-29)."""
    derived = _derive(
        "heat_pump", {"band_k": 1.5, "area_m2": 60.0, "rated_kw": 3.0, "comfort_c": 21.0}
    )
    assert derived.strategy_params["bank_k"] == 1.5
    assert derived.strategy_params["coast_k"] == 1.5


def test_52_every_heating_type_offers_both_and_defaults_to_moving_heat() -> None:
    """`best_save` stays a choice for heating, never the default (D-0738)."""
    for type_key in ("floor_heating", "heat_pump", "radiator"):
        device_type = device_types.get(type_key)
        assert {"heat_capacitor", "best_save"} <= set(device_type.strategies)
        assert device_type.default_strategy == "heat_capacitor"
