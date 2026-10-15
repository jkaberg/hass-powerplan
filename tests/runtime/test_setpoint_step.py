"""A thermostat's declared step is the finest setpoint a plan asks of it (D-0818)."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.powerplan.const import (
    LOAD_BINDINGS,
    LOAD_PARAMS,
    LOAD_PROFILE,
    LOAD_TYPE,
)
from custom_components.powerplan.core.loads.kinds.setpoint import DEFAULT_STEP_K
from custom_components.powerplan.runtime import declared_setpoint_step, load_from_subentry
from tests.core.loads.conftest import FLOOR_PARAMS, REFERENCE_PROFILE


def _row(role: str, **changes: Any) -> dict[str, Any]:
    return {"role": role, "entity_id": f"climate.{role}", "writable": True, **changes}


def _data(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        LOAD_TYPE: "floor_heating",
        LOAD_PROFILE: "generic_climate",
        LOAD_BINDINGS: list(rows),
        LOAD_PARAMS: dict(FLOOR_PARAMS),
        "strategy": "heat_capacitor",
    }


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (_row("setpoint", step=0.1), 0.1),
        (_row("setpoint", step=0.5), 0.5),
        (_row("setpoint", step=1, scale=0.1), 0.1),
        (_row("setpoint"), None),
        (_row("temp", step=0.1), None),
    ],
)
def test_the_step_a_setpoint_binding_declares_in_kelvin(
    row: dict[str, Any], expected: float | None
) -> None:
    """The entity's `target_temp_step` in powerplan's unit; a Z-TRM declares none."""
    assert declared_setpoint_step(_data(row)) == expected


def test_a_load_built_from_a_subentry_takes_its_thermostats_step() -> None:
    """The kind quantises the plan's delta to what the entity declared, else to 0.5 K."""
    declared = load_from_subentry(
        "loop", "Loop", _data(_row("setpoint", step=0.1)), REFERENCE_PROFILE
    )
    assert declared.kind.step_k == pytest.approx(0.1)  # type: ignore[attr-defined]
    silent = load_from_subentry("loop", "Loop", _data(_row("setpoint")), REFERENCE_PROFILE)
    assert silent.kind.step_k == pytest.approx(DEFAULT_STEP_K)  # type: ignore[attr-defined]
