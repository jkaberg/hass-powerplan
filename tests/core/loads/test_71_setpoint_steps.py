"""D4 §9 71 - a `SETPOINT` kind writes the plan's delta in whole steps of the device's own (D-0818).

A thermostat takes its setpoint in steps, so a delta finer than that is a write the device
turns into another number, or into the same one. The kind rounds the plan's delta to its step
before it clamps, and the gate's tolerance then sees a different setpoint only when the device
would.
"""

from __future__ import annotations

import pytest

from tests.core.loads.conftest import kind_ctx, setpoint_kind

TARGET = 21.0


def _written(step_k: float, delta: float) -> float:
    kind = setpoint_kind(shed_setpoint=18.0, step_k=step_k, band_up=2.0, band_down=2.0)
    q = kind.quantise(0.0, kind_ctx(target=TARGET, setpoint_delta=delta))
    assert q.value is not None
    return float(q.value)


@pytest.mark.parametrize(
    ("step", "delta", "written"),
    [
        (0.5, 0.2, 21.0),
        (0.5, 0.3, 21.5),
        (0.5, -0.74, 20.5),
        (0.5, -0.76, 20.0),
        (0.1, 0.34, 21.3),
        (0.05, 0.34, 21.35),
    ],
)
def test_71_the_delta_is_written_in_whole_steps(step: float, delta: float, written: float) -> None:
    """The nearest step to the plan's delta, from the target."""
    assert _written(step, delta) == pytest.approx(written)


def test_71_a_delta_within_a_step_of_zero_is_the_target() -> None:
    """Nothing finer than the device's step is written, so nothing finer is a change."""
    assert _written(0.5, 0.24) == _written(0.5, 0.0) == TARGET
