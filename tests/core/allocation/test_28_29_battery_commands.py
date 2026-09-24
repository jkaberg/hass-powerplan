"""D6 §9 28–29 - a battery's slot becomes its command; a battery without self-use is balanced.

INV-30's three answers reach a battery as four commands (D4 §4.2): the
walk records what the plan said about the slot on the grant (`Grant.answer`),
and the battery kind turns it into self-use (`None`), hold (`0`), or a charge or
discharge. A planned 0 is a hold: it follows the measured surplus and never
discharges. A battery with no self-use of its own - a bare number - is balanced
by the walk in a free slot, as an inverter would balance itself.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from custom_components.powerplan.core.allocation import AllocCfg, AllocState, allocate
from custom_components.powerplan.core.loads.kinds import BatteryCfg, BatteryCommand, BatteryKind
from custom_components.powerplan.core.model import PlanAnswer
from custom_components.powerplan.core.strategies import LoadView
from tests.core.allocation.conftest import (
    NOW,
    alloc_ctx,
    budget_of,
    controlled,
    demand,
    meter,
    plan_of,
)
from tests.core.loads.conftest import kind_ctx

KIND = BatteryKind(
    BatteryCfg(commands=frozenset(BatteryCommand), charge_w=5000.0, discharge_w=5000.0)
)


def _battery(*, self_use: bool = True, **kwargs: Any) -> LoadView:
    """Return a 5 kW battery at 60 %: the four commands, or a bare number without self-use."""
    options: dict[str, Any] = {
        "load_id": "battery",
        "priority": 30,
        "strategy": "peak_shave",
        "demand": demand(min_w=-5000.0, max_w=5000.0, import_w=0.0, reason="60 % of 100 %"),
        "nameplate_w": 5000.0,
        "kind": "battery" if self_use else "modulate",
        "params": {"chemistry": "lfp", "self_use": self_use},
        "phases": 3,
    }
    options.update(kwargs)
    return LoadView(**options)


def _tick(
    battery: LoadView,
    *,
    grid_w: float,
    envelope_w: float | None = None,
    stage: int = 0,
    sunny: bool = False,
) -> Any:
    plans = (
        {}
        if envelope_w is None
        else {"battery": plan_of("battery", (-5, envelope_w), (10, envelope_w))}
    )
    state = AllocState(sun_since={"battery": NOW - timedelta(minutes=5)}) if sunny else AllocState()
    ctx = alloc_ctx(
        [battery],
        budget=budget_of(8000.0),
        plans=plans,
        views={"battery": controlled("battery", measured_w=0.0)},
        meter_snapshot=meter(grid_w=grid_w),
        stage=stage,
    )
    grants, _, _ = allocate(ctx, (), AllocCfg(), state)
    return grants["battery"]


def _command(grant: Any) -> str:
    context = kind_ctx(answer=grant.answer, shed=grant.shed)
    return str(KIND.quantise(grant.w, context).value)


@pytest.mark.inv("INV-30")
def test_28_a_free_slot_is_the_inverter_s_self_use() -> None:
    """No plan: the grant says so, and the battery is left to balance itself."""
    grant = _tick(_battery(), grid_w=800.0)

    assert grant.answer is PlanAnswer.NONE
    assert _command(grant) == "self_use"


@pytest.mark.inv("INV-30")
def test_28_a_planned_zero_is_a_hold_that_takes_the_sun_and_never_discharges() -> None:
    """At noon with 1.2 kW exported, a hold charges on the sun; importing, it gives nothing."""
    sunny = _tick(_battery(), grid_w=-1200.0, envelope_w=0.0, sunny=True)
    dark = _tick(_battery(), grid_w=900.0, envelope_w=0.0)

    assert sunny.answer is PlanAnswer.HOLD
    assert sunny.w == pytest.approx(1200.0)
    assert dark.w == pytest.approx(0.0), "a hold never discharges into the import"
    assert _command(sunny) == _command(dark) == "hold"


def test_28_a_planned_power_is_a_charge_or_a_discharge() -> None:
    """A planned charge is charged; a planned discharge displaces import, never more."""
    charge = _tick(_battery(), grid_w=500.0, envelope_w=3000.0)
    discharge = _tick(_battery(), grid_w=1500.0, envelope_w=-3000.0)

    assert charge.answer is PlanAnswer.POWER
    assert _command(charge) == "charge:3000"
    assert discharge.w == pytest.approx(-1500.0)
    assert _command(discharge) == "discharge:1500"


def test_28_at_stage_one_the_discharge_overrides_a_hold() -> None:
    """2 kW over the allowance at stage 1: the tick discharges, whatever the plan said."""
    grant = _tick(_battery(), grid_w=10_000.0, envelope_w=0.0, stage=1)

    assert grant.w == pytest.approx(-2000.0)
    assert _command(grant).startswith("discharge:")


def test_28_a_row_without_a_hold_self_uses_instead() -> None:
    """A plug-in battery with no self-use of its own holds; a row with no hold self-uses."""
    no_hold = BatteryKind(
        BatteryCfg(
            commands=frozenset({BatteryCommand.SELF_USE, BatteryCommand.CHARGE}),
            charge_w=5000.0,
            discharge_w=5000.0,
            commanded=False,
        )
    )
    context = kind_ctx(answer=PlanAnswer.HOLD)

    assert no_hold.quantise(0.0, context).value == "self_use"
    assert no_hold.quantise(-3000.0, kind_ctx(answer=PlanAnswer.POWER)).value == "self_use", (
        "no discharge command: its own self-use serves the slot"
    )
    assert no_hold.quantise(2000.0, kind_ctx(answer=PlanAnswer.POWER)).value == "self_use", (
        "an inverter that sets its own power charges at its rate or not at all"
    )
    assert no_hold.quantise(5000.0, kind_ctx(answer=PlanAnswer.POWER)).value == "charge"


def test_29_a_bare_number_battery_balances_the_house_in_a_free_slot() -> None:
    """Importing 800 W, a battery with no self-use of its own is told −800 W; never into export."""
    importing = _tick(_battery(self_use=False), grid_w=800.0)
    exporting = _tick(_battery(self_use=False), grid_w=-600.0, sunny=True)

    assert importing.w == pytest.approx(-800.0)
    assert exporting.w == pytest.approx(600.0), "the sun charges it, as before"


def test_29_at_its_reserve_a_bare_number_battery_gives_nothing() -> None:
    """At the reserve the demand offers no discharge (`min_w = 0`): the walk does not balance it."""
    at_reserve = _battery(
        self_use=False, demand=demand(min_w=0.0, max_w=5000.0, import_w=0.0, reason="at reserve")
    )

    assert _tick(at_reserve, grid_w=800.0).w >= 0.0
