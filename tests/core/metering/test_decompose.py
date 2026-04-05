"""D3 §9 8, 9, 13 - decomposition: σ on uncontrolled, settling, surplus.

σ measured on total power makes our own shedding inflate the reserve, which
triggers more shedding (INV-16); a load whose write is still settling is counted
at what was commanded, not at its lagging sensor (INV-18); export, production
and surplus are first-class signed readings (INV-19).
"""

import math
from datetime import datetime, timedelta

import pytest

from custom_components.powerplan.core.metering import (
    ControlledView,
    MeterSample,
    Quality,
    Reading,
    WindowMeter,
    controlled_power,
    uncontrolled,
)
from tests.builders import histories
from tests.core.metering.conftest import config, drive, local

START = local(2026, 1, 15, 19, 0)


def _sigma_of(
    base: histories.Trace, extra_w: float, declared: bool, *, lag_s: float = 0.0
) -> float:
    """σ over a 20-minute run, with `extra_w` of controlled load in the second half."""
    switch_on = START + timedelta(minutes=10)
    grid = histories.add(base, extra_w, switch_on, base.end)

    def views(now: datetime) -> tuple[ControlledView, ...]:
        if not declared:
            return ()
        # The charger's own measurement lags the site meter by `lag_s`; while it
        # does, the write is settling and the commanded value is what counts
        # (INV-18). That is what keeps σ clean through the step.
        settled_at = switch_on + timedelta(seconds=lag_s)
        return (
            ControlledView(
                load_id="ev",
                measured_w=extra_w if now >= settled_at else 0.0,
                commanded_w=extra_w if now >= switch_on else 0.0,
                settling=switch_on <= now < settled_at,
                phases=None,
            ),
        )

    run = drive(WindowMeter(config(), None), grid, controlled=views)
    sigma = run.last.sigma_uncontrolled_w
    assert sigma is not None
    return sigma


@pytest.mark.inv("INV-16")
def test_08_sigma_is_measured_on_uncontrolled_power() -> None:
    """A 3 kW controlled step moves σ by < 5 %; on total power it moves it hugely."""
    base = histories.noisy(START, minutes=20, base_w=1500.0, amplitude_w=400.0, step_s=30.0)
    quiet = _sigma_of(base, 0.0, declared=False)
    declared = _sigma_of(base, 3000.0, declared=True, lag_s=30.0)
    undeclared = _sigma_of(base, 3000.0, declared=False)

    assert declared == pytest.approx(quiet, rel=0.05), (
        f"σ moved from {quiet:.0f} W to {declared:.0f} W on a load we measure"
    )
    assert undeclared > quiet * 1.5, (
        "the test cannot see the bug it exists to prevent: σ on total power "
        f"is {undeclared:.0f} W against {quiet:.0f} W"
    )


def test_08b_sigma_reports_the_default_until_it_has_enough_samples() -> None:
    """After a restart the buffer is empty: D6 must see it is on a default (D3 §5.9)."""
    cfg = config(sigma_min_samples=10, sigma_default_w=800.0)
    base = histories.noisy(START, minutes=2, base_w=1500.0, amplitude_w=400.0, step_s=30.0)
    run = drive(WindowMeter(cfg, None), base)
    assert run.snapshots[2].sigma_samples < 10
    assert run.snapshots[2].sigma_uncontrolled_w == pytest.approx(800.0)
    assert run.last.sigma_samples == len(run.snapshots)

    # Two entities updating in the same event-loop pass give two samples with one
    # timestamp; σ must still be a number.
    again = run.meter.sample(
        run.last.now, MeterSample(grid_w=Reading(1500.0, at=run.last.now, source="test")), ()
    )
    assert again.sigma_uncontrolled_w is not None
    assert again.sigma_uncontrolled_w >= 0.0


@pytest.mark.inv("INV-18")
def test_09_settling_uses_commanded_power() -> None:
    """settling=True, commanded 1 kW, measured 3 kW ⇒ uncontrolled uses 1 kW."""
    view = ControlledView(
        load_id="ev", measured_w=3000.0, commanded_w=1000.0, settling=True, phases=None
    )
    assert controlled_power(view) == 1000.0
    assert uncontrolled(9000.0, (view,)) == 8000.0

    settled = ControlledView(
        load_id="ev", measured_w=3000.0, commanded_w=1000.0, settling=False, phases=None
    )
    assert controlled_power(settled) == 3000.0
    assert uncontrolled(9000.0, (settled,)) == 6000.0


def test_09b_an_unmetered_controlled_load_counts_as_zero_and_says_so() -> None:
    """No measurement and no command ⇒ 0 W, flagged in health (D3 §5.8)."""
    view = ControlledView(
        load_id="tank", measured_w=None, commanded_w=None, settling=False, phases=None
    )
    assert controlled_power(view) == 0.0

    now = local(2026, 1, 15, 19, 0, 30)
    snap = WindowMeter(config(), None).sample(
        now, MeterSample(grid_w=Reading(4000.0, at=now, source="test")), (view,)
    )
    assert snap.uncontrolled_w == pytest.approx(4000.0)
    assert snap.health.unmetered_controlled == ("tank",)


def test_09c_settling_with_no_command_falls_back_to_the_measurement() -> None:
    """A load that is settling but has never been commanded is not invisible."""
    view = ControlledView(
        load_id="ev", measured_w=2500.0, commanded_w=None, settling=True, phases=None
    )
    assert controlled_power(view) == 2500.0


@pytest.mark.inv("INV-19")
def test_13_surplus_is_export_plus_battery_charge_smoothed() -> None:
    """Surplus = max(0, −grid) + battery_charge, through a 60 s EMA (D3 §2)."""
    cfg = config(surplus_tau_s=60.0)
    trace = histories.constant(START, minutes=10, watts=-2000.0, step_s=60.0)
    run = drive(WindowMeter(cfg, None), trace, battery_w=1500.0)

    first = run.snapshots[0]
    assert first.export_w == pytest.approx(2000.0)
    assert first.import_w == 0.0
    assert first.surplus_w == pytest.approx(3500.0)  # the EMA starts at the first value

    # A step into export is smoothed: one 60 s step of a 60 s EMA covers
    # 1 − e⁻¹ of the distance, not all of it. D5 decides how long surplus must
    # persist before it acts; D3 only has to stop it being a spike.
    stepped = histories.steps(START, 60.0, [0.0] * 5 + [-3500.0] * 5)
    run2 = drive(WindowMeter(cfg, None), stepped, battery_w=0.0)
    assert run2.snapshots[4].surplus_w == pytest.approx(0.0)
    assert run2.snapshots[5].surplus_w == pytest.approx(3500.0 * (1 - math.exp(-1.0)), rel=0.02)
    assert run2.snapshots[9].surplus_w > 3300.0


@pytest.mark.inv("INV-19")
def test_13b_consumption_is_partial_while_exporting_without_a_production_sensor() -> None:
    """Consumption = grid + production; unknown while exporting without one (D3 §2)."""
    cfg = config()
    now = local(2026, 6, 15, 12, 0, 30)

    metered = WindowMeter(cfg, None).sample(
        now,
        MeterSample(
            grid_w=Reading(-2000.0, at=now, source="grid"),
            production_w=Reading(5000.0, at=now, source="pv"),
        ),
        (),
    )
    assert metered.production_w == pytest.approx(5000.0)
    assert metered.consumption_w == pytest.approx(3000.0)
    assert metered.consumption_quality is Quality.OK
    assert metered.health.production_known is True

    blind = WindowMeter(cfg, None).sample(
        now, MeterSample(grid_w=Reading(-2000.0, at=now, source="grid")), ()
    )
    assert blind.consumption_w == pytest.approx(0.0)
    assert blind.consumption_quality is Quality.PARTIAL
    assert blind.health.production_known is False

    importing = WindowMeter(cfg, None).sample(
        now, MeterSample(grid_w=Reading(4000.0, at=now, source="grid")), ()
    )
    assert importing.consumption_w == pytest.approx(4000.0)
    assert importing.consumption_quality is Quality.OK
