"""D9 §5.3 - the phase-0 rows of the scenario catalogue, asserted (D9 §9 1–3).

Five days on the reference house through the whole engine at 10 s. Each row
names one behaviour and fails for one reason; the numbers come from the D4
defaults the house is built with and from the simulators' own constants, never
from a run that happened to pass. Everything is seeded: a flaky scenario is a
bug (D9 §8), which is what the byte-identity test at the end is for.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.loads.types.water_heater import READY_BAND_K
from tests.scenarios import catalogue
from tests.scenarios.runner import run_scenario
from tests.sim.tank import THERMOSTAT_HYSTERESIS_K

if TYPE_CHECKING:
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario

#: The house's bathrooms: comfort 24 °C, floor 21 °C (D4 §6.1, `tests/builders/houses.py`).
BATHROOM_FLOOR_C = 21.0
#: The tank's ready temperature (D9 §5.9) and the two bands that bound "reached":
#: the controller's (D4 §5.12) and the electronic thermostat's differential (`sim/tank.py`).
TANK_READY_C = 75.0
TANK_REACHED_C = TANK_READY_C - max(READY_BAND_K, THERMOSTAT_HYSTERESIS_K)
#: The EV's target (D4 §6.2 default, `EV_PARAMS`).
EV_TARGET_SOC = 0.80
#: Z-Wave: one command per device per ten minutes (D4 §5.10 profile table, D9 §5.3).
ZWAVE_MAX_PER_10_MIN = 1


@pytest.fixture(scope="module")
def winter() -> ScenarioResult:
    """Run `reference_winter_day` once for the module."""
    return run_scenario(catalogue.reference_winter_day())


@pytest.fixture(scope="module")
def flat() -> ScenarioResult:
    """Run `flat_price_night` once for the module."""
    return run_scenario(catalogue.flat_price_night())


@pytest.fixture(scope="module")
def autumn() -> ScenarioResult:
    """Run `dst_autumn` once for the module."""
    return run_scenario(catalogue.dst_autumn())


@pytest.fixture(scope="module")
def spring() -> ScenarioResult:
    """Run `dst_spring` once for the module."""
    return run_scenario(catalogue.dst_spring())


@pytest.fixture(scope="module")
def outage() -> ScenarioResult:
    """Run `price_outage_48h` once for the module."""
    return run_scenario(catalogue.price_outage_48h())


def _windows_on(result: ScenarioResult, day: date, tz: object) -> list[datetime]:
    """Return the closed windows whose local start falls on `day`."""
    starts = [datetime.fromisoformat(start) for start in result.window_starts]
    return [start for start in starts if start.astimezone(tz).date() == day]  # type: ignore[arg-type]


def _consecutive(starts: list[datetime], window: timedelta) -> bool:
    """Whether every window follows the one before it by exactly one window."""
    return all(b - a == window for a, b in pairwise(starts))


# --------------------------------------------------------------------------- #
# reference_winter_day
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase0_winter")
@pytest.mark.inv("INV-1")
def test_winter_day_no_window_over_target(winter: ScenarioResult) -> None:
    """A cold Tuesday with the EV, the tank and two bathrooms under a 10 kW target."""
    assert winter.engine_failures == 0
    assert winter.over_target == 0
    assert winter.windows == 24


@pytest.mark.xdist_group(name="phase0_winter")
@pytest.mark.inv("INV-28")
def test_winter_day_the_car_is_charged_by_departure(winter: ScenarioResult) -> None:
    """EV at 80 % by the 07:30 departure, no session dropped (INV-28)."""
    assert winter.deadline_misses == 0
    assert winter.ev_soc_at_departure is not None
    assert winter.ev_soc_at_departure >= EV_TARGET_SOC - 1e-6
    assert winter.sessions_dropped == 0


@pytest.mark.xdist_group(name="phase0_winter")
def test_winter_day_the_tank_is_ready_by_half_past_six(winter: ScenarioResult) -> None:
    """At its ready temperature within the thermostat's differential (D-0256)."""
    assert winter.tank_top_at_ready is not None
    assert winter.tank_top_at_ready >= TANK_REACHED_C


@pytest.mark.xdist_group(name="phase0_winter")
@pytest.mark.inv("INV-55")
def test_winter_day_the_bathrooms_never_fall_below_their_floor(winter: ScenarioResult) -> None:
    """The floor is physical: 21 °C as the thermostat reports it (INV-55, D-0259)."""
    assert winter.comfort_violation_min == 0.0
    assert winter.bathroom_min_c >= BATHROOM_FLOOR_C


@pytest.mark.xdist_group(name="phase0_winter")
@pytest.mark.inv("INV-58")
def test_winter_day_zwave_gets_one_command_per_ten_minutes(winter: ScenarioResult) -> None:
    """Two Heatit loops on Z-Wave: at most one command each per ten minutes."""
    for loop in ("loop_bath_1", "loop_bath_2"):
        assert winter.max_writes_per_10min[loop] <= ZWAVE_MAX_PER_10_MIN
    assert winter.zero_amp_writes == 0


@pytest.mark.xdist_group(name="phase0_winter")
@pytest.mark.inv("INV-32")
def test_winter_day_no_unforced_commitment_break(winter: ScenarioResult) -> None:
    """A re-cut never reneges on a committed slot with the same inputs (INV-32)."""
    assert winter.commitment_breaks == {}
    assert winter.plan_gaps == 0


# --------------------------------------------------------------------------- #
# flat_price_night
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase0_flat")
@pytest.mark.inv("INV-32")
def test_flat_night_the_plan_does_not_churn(flat: ScenarioResult) -> None:
    """Norgespris: every slot costs the same, so nothing may be re-decided (INV-32)."""
    assert flat.engine_failures == 0
    assert flat.commitment_breaks == {}
    assert flat.plan_gaps == 0
    assert flat.plan_runs_max["ev"] == 1, "one contiguous run, the earliest slots first"


@pytest.mark.xdist_group(name="phase0_flat")
@pytest.mark.inv("INV-39")
def test_flat_night_the_car_charges_contiguously_and_stops_once(flat: ScenarioResult) -> None:
    """The EV charges in one run and stops when done - no start/stop churn (INV-39).

    Norgespris flattens the energy price, not the grid's energiledd: night is
    still cheaper (Tensio `natt` from 22:00), so the plan may pause the charger
    on arrival for a five-hour idle block - a plan stop past INV-39's minimum -
    and stops it once more when the session is done. Nothing in between.
    """
    assert flat.ev_stops <= 2, "the pause on arrival for the cheap night, and the stop when done"
    assert flat.zero_amp_writes == 0
    assert flat.deadline_misses == 0
    assert flat.ev_soc_at_departure is not None
    assert flat.ev_soc_at_departure >= EV_TARGET_SOC - 1e-6


@pytest.mark.xdist_group(name="phase0_flat")
def test_flat_night_comfort_and_the_ceiling_hold(flat: ScenarioResult) -> None:
    """The flat night still keeps the ceiling, the bathrooms and the tank."""
    assert flat.over_target == 0
    assert flat.comfort_violation_min == 0.0
    assert flat.tank_top_at_ready is not None
    assert flat.tank_top_at_ready >= TANK_REACHED_C


# --------------------------------------------------------------------------- #
# dst_autumn / dst_spring
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase0_autumn")
@pytest.mark.inv("INV-7")
def test_dst_autumn_has_twenty_five_windows(autumn: ScenarioResult) -> None:
    """The 25-hour day: 25 windows, none missing, none doubled (D3, INV-7)."""
    tz = catalogue.OSLO
    starts = _windows_on(autumn, date(2026, 10, 25), tz)
    assert len(starts) == 25
    assert _consecutive(starts, timedelta(hours=1))
    assert len(set(autumn.window_starts)) == autumn.windows
    assert autumn.over_target == 0
    assert autumn.engine_failures == 0


@pytest.mark.xdist_group(name="phase0_autumn")
@pytest.mark.inv("INV-32")
def test_dst_autumn_plans_without_gaps_or_churn(autumn: ScenarioResult) -> None:
    """The 25-hour night opens no hole in any plan and re-decides nothing."""
    assert autumn.plan_gaps == 0
    assert autumn.commitment_breaks == {}
    assert autumn.comfort_violation_min == 0.0
    assert autumn.zero_amp_writes == 0


@pytest.mark.xdist_group(name="phase0_spring")
@pytest.mark.inv("INV-7")
def test_dst_spring_has_twenty_three_windows(spring: ScenarioResult) -> None:
    """The 23-hour day, in the household's Easter week (D4 §5.12: ready-by dropped)."""
    tz = catalogue.OSLO
    starts = _windows_on(spring, date(2027, 3, 28), tz)
    assert len(starts) == 23
    assert _consecutive(starts, timedelta(hours=1))
    assert len(set(spring.window_starts)) == spring.windows
    assert spring.over_target == 0
    assert spring.engine_failures == 0


@pytest.mark.xdist_group(name="phase0_spring")
@pytest.mark.inv("INV-32")
def test_dst_spring_plans_without_gaps_or_churn(spring: ScenarioResult) -> None:
    """The 23-hour night opens no hole in any plan and re-decides nothing."""
    assert spring.plan_gaps == 0
    assert spring.commitment_breaks == {}
    assert spring.comfort_violation_min == 0.0
    for loop in ("loop_bath_1", "loop_bath_2"):
        assert spring.max_writes_per_10min[loop] <= ZWAVE_MAX_PER_10_MIN


# --------------------------------------------------------------------------- #
# price_outage_48h
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase0_outage")
@pytest.mark.inv("INV-5")
def test_outage_plans_on_the_synthesised_floor(outage: ScenarioResult) -> None:
    """Two days without prices: the last forecaster's floor, and it still knows night."""
    assert outage.engine_failures == 0
    assert outage.synthesised_plans > 0
    assert outage.hysteresis_doubled
    assert outage.outage_night_kwh > outage.outage_day_kwh


@pytest.mark.xdist_group(name="phase0_outage")
def test_outage_the_house_still_holds(outage: ScenarioResult) -> None:
    """Without prices the ceiling, the deadlines and the comfort floors still hold."""
    assert outage.over_target == 0
    assert outage.deadline_misses == 0
    assert outage.comfort_violation_min == 0.0
    assert outage.commitment_breaks == {}


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


@pytest.mark.xdist_group(name="phase0_flat")
def test_a_scenario_is_byte_identical_across_runs(flat: ScenarioResult) -> None:
    """Seeded noise: the same day twice is the same day (D9 §8)."""
    again = run_scenario(catalogue.flat_price_night())
    assert again.digest == flat.digest
