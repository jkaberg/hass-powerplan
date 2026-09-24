"""D9 §5.3 `battery_holds_for_peak` - a planned hold keeps the charge (PLAN §7 dec. 44).

The dark January day of `pv_battery_peak_shave_winter`, with a battery whose
inverter balances the house by itself (`tests/sim/battery.py`'s commanded
battery): self-use in a free slot, a hold where the plan keeps the charge for a
later slot. Run on a commanded-power row (SolaX's shape) and on a row whose
inverter sets the power (GoodWe's), and once more with an inverter that has no hold,
which and sent its own self-use instead - the
regression this scenario guards.

On this day the hold keeps the charge through the night, and
no window goes over target with or without it; the old inverter spends the
charge in the very slots its plan held, about sixty times the energy per held
tick, and loses most of its holds to the replans that follow. So the scenario
asserts what the hold does, per tick, rather than a window the day never
threatens (D-0671).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import TICK_S, ScenarioResult, run_scenario

if TYPE_CHECKING:
    from datetime import datetime

KWH_PER_W_TICK = TICK_S / 3600.0 / 1000.0


@dataclass
class _Held:
    """Per run: the ticks the plan held, and what the battery gave the house in them."""

    ticks: int = 0
    out_kwh: float = 0.0

    def __call__(self, now: datetime, snapshot: Any) -> None:
        del now
        status = snapshot.loads.get("battery")
        plan = snapshot.plans.get("battery")
        if status is None or plan is None or plan.cap_now_w != 0.0:
            return
        self.ticks += 1
        self.out_kwh += max(0.0, -(status.measured_w or 0.0)) * KWH_PER_W_TICK

    @property
    def per_tick(self) -> float:
        """Return the energy given per held tick, kWh."""
        return self.out_kwh / self.ticks if self.ticks else 0.0


def _run(**kwargs: Any) -> tuple[ScenarioResult, _Held]:
    scenario = catalogue.battery_holds_for_peak(**kwargs)

    def run() -> tuple[ScenarioResult, _Held]:
        held = _Held()
        return run_scenario(scenario, held), held

    return cached(__file__, scenario.name, run)


@pytest.fixture(scope="module")
def commanded() -> tuple[ScenarioResult, _Held]:
    """Run the commanded-power row once for the module."""
    return _run()


@pytest.fixture(scope="module")
def mode() -> tuple[ScenarioResult, _Held]:
    """Run the row whose inverter sets the power once for the module."""
    return _run(row="inverter")


@pytest.fixture(scope="module")
def floor() -> tuple[ScenarioResult, _Held]:
    """Run a floor row with no discharge command - a Powerwall's shape."""
    return _run(row="floor")


@pytest.fixture(scope="module")
def old() -> tuple[ScenarioResult, _Held]:
    """Run the inverter from before WP7.9's rows once for the module."""
    return _run(hold_as_self_use=True)


@pytest.mark.inv("INV-30")
@pytest.mark.parametrize("which", ["commanded", "mode", "floor"])
def test_holds_for_peak_the_battery_keeps_its_charge_while_held(
    which: str, request: pytest.FixtureRequest
) -> None:
    """Held slots exist, the battery gives next to nothing in them, and no window is over."""
    result, held = request.getfixturevalue(which)

    assert result.over_target == 0
    assert held.ticks > 0, "the plan held the charge somewhere on this day"
    assert held.per_tick < 1e-4, "a held battery gives the house nothing but a slot edge's lag"


@pytest.mark.inv("INV-30")
def test_holds_for_peak_the_old_inverter_spends_what_its_plan_held(
    commanded: tuple[ScenarioResult, _Held], old: tuple[ScenarioResult, _Held]
) -> None:
    """The regression: a planned hold sent as self-use gives away the charge it was to keep."""
    _, new = commanded
    _, before = old

    assert before.per_tick > 10 * new.per_tick
