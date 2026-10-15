"""D5 §5.8 / D-0837 - the battery's plan covers the slots the ceiling is short in.

The dark January day of `pv_battery_peak_shave_winter` under a 5 kW target, with a
planner baseline of 7 kW from 17:00 to 19:00 (`battery_covers_the_evening`). The
baseline alone is over the ceiling the ladder defends there, so `Headroom.deficit_w`
is positive in those slots and the `peak_shave` battery is planned to discharge in
them. The plan is read tick by tick, before D6's live ladder acts on the window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest

from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import ScenarioResult, run_scenario

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.powerplan.core.model import Plan


@dataclass
class _Plans:
    """The battery's evening slots in the last plan built before 17:00 local, by slot start."""

    slots: dict[datetime, Any] = field(default_factory=dict)
    built_at: datetime | None = None

    def __call__(self, now: datetime, plans: Mapping[str, Plan]) -> None:
        plan = plans.get("battery")
        local = now.astimezone(catalogue.OSLO)
        if plan is None or local.hour >= catalogue.EVENING_HOURS[0] or local.day != 12:
            return
        low, high = catalogue.EVENING_HOURS
        found = {
            s.start: s
            for s in plan.slots
            if low <= s.start.astimezone(catalogue.OSLO).hour < high
            and s.start.astimezone(catalogue.OSLO).day == 12
        }
        if found:
            self.slots, self.built_at = found, now


def _run() -> tuple[ScenarioResult, _Plans]:
    scenario = catalogue.battery_covers_the_evening()

    def run() -> tuple[ScenarioResult, _Plans]:
        plans = _Plans()
        return run_scenario(scenario, plan_observer=plans), plans

    return cached(__file__, scenario.name, run)


@pytest.fixture(scope="module")
def evening() -> tuple[ScenarioResult, _Plans]:
    """Run the evening once for the module."""
    return _run()


@pytest.mark.inv("INV-31")
def test_47_the_evening_is_covered_by_the_plan(evening: tuple[ScenarioResult, _Plans]) -> None:
    """Each of the eight deficit quarters is a planned discharge at least the ceiling's shortfall.

    The baseline is 7 kW against a 5 kW target the ladder defends at a fraction of
    (less ε): the shortfall is at least 2 kW in every quarter from 17:00 to 19:00, and
    the plan built before 17:00 (16:45) discharges 2.25-2.7 kW in each of them.
    """
    result, plans = evening
    assert result.engine_failures == 0
    assert plans.built_at is not None
    assert len(plans.slots) == 8
    for slot in plans.slots.values():
        assert slot.envelope_w is not None
        assert -slot.envelope_w >= 2000.0
        assert slot.reason == "discharge"
    assert -sum(slot.kwh for slot in plans.slots.values()) >= 4.0
