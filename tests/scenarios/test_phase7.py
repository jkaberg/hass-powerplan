"""D9 §5.3's Phase 7 rows: panels, the battery, and a negative export price.

The reference house with 8 kWp on the roof, its production forecast to the
engine from the simulator that makes it (`runner._pv_forecast`), and an export
curve (`House.export`):

- `pv_no_battery_ev_waits`: a spring Sunday, the car home, Monday's 07:30
  deadline. The midday surplus, priced at the spot it would have been sold for,
  is cheaper than the night's import price, so the car charges on the sun and
  not at night, and makes its deadline.
- `pv_battery_self_consumption`: a June weekday, a 10 kWh battery, a flat
  0.10 NOK feed-in. The battery charges from the surplus and imports nothing
  for it, then discharges into the house's own import, never into export.
- `pv_battery_peak_shave_winter`: the dark January day under a 9 kW target.
  `peak_shave` charges its reserve from the grid where its plan says so and
  discharges at the evening peak: no window over the target, the car on time.
- `negative_price_soak`: a June Saturday whose spot goes below zero for six
  hours. Through them the tank and the battery take the surplus, so almost
  nothing is exported at a loss; nothing is ever curtailed (the panels always
  produce what the sun gives).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest

from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import TICK_S, ScenarioResult, run_scenario

if TYPE_CHECKING:
    from collections.abc import Callable

HOURS_PER_TICK = TICK_S / 3600.0


@dataclass
class _Trail:
    """Per tick: the grid, the battery, the car and the tank, in W (picklable)."""

    at: list[datetime] = field(default_factory=list)
    grid_w: list[float] = field(default_factory=list)
    battery_w: list[float] = field(default_factory=list)
    ev_w: list[float] = field(default_factory=list)
    tank_w: list[float] = field(default_factory=list)

    def __call__(self, now: datetime, snapshot: Any) -> None:
        if snapshot.meter is None:
            return
        self.at.append(now)
        self.grid_w.append(snapshot.meter.grid_w)
        self.battery_w.append(_measured(snapshot, "battery"))
        self.ev_w.append(_measured(snapshot, "ev"))
        self.tank_w.append(_measured(snapshot, "tank"))

    def kwh(self, values: list[float], keep: Callable[[int], bool] | None = None) -> float:
        """Return the energy of `values` over the ticks `keep` selects."""
        return sum(
            value * HOURS_PER_TICK / 1000.0
            for index, value in enumerate(values)
            if keep is None or keep(index)
        )


def _measured(snapshot: Any, load_id: str) -> float:
    status = snapshot.loads.get(load_id)
    return 0.0 if status is None or status.measured_w is None else status.measured_w


def _run(name: str) -> tuple[ScenarioResult, _Trail]:
    def run() -> tuple[ScenarioResult, _Trail]:
        trail = _Trail()
        return run_scenario(getattr(catalogue, name)(), trail), trail

    return cached(__file__, name, run)


@pytest.fixture(scope="module")
def ev_waits() -> tuple[ScenarioResult, _Trail]:
    """Run `pv_no_battery_ev_waits` once for the module."""
    return _run("pv_no_battery_ev_waits")


@pytest.fixture(scope="module")
def self_consumption() -> tuple[ScenarioResult, _Trail]:
    """Run `pv_battery_self_consumption` once for the module."""
    return _run("pv_battery_self_consumption")


@pytest.fixture(scope="module")
def winter() -> tuple[ScenarioResult, _Trail]:
    """Run `pv_battery_peak_shave_winter` once for the module."""
    return _run("pv_battery_peak_shave_winter")


@pytest.fixture(scope="module")
def au_soak() -> tuple[ScenarioResult, _Trail]:
    """Run `au_solar_soak` once for the module."""
    return _run("au_solar_soak")


@pytest.fixture(scope="module")
def saldering() -> tuple[ScenarioResult, _Trail]:
    """Run `nl_saldering_end` once for the module."""
    return _run("nl_saldering_end")


@pytest.fixture(scope="module")
def soak() -> tuple[ScenarioResult, _Trail]:
    """Run `negative_price_soak` once for the module."""
    return _run("negative_price_soak")


def _local_hour(at: datetime) -> int:
    return at.astimezone(catalogue.OSLO).hour


@pytest.mark.xdist_group(name="phase7_ev_waits")
def test_pv_no_battery_ev_waits_charges_on_the_sun_and_makes_its_deadline(
    ev_waits: tuple[ScenarioResult, _Trail],
) -> None:
    """Every kWh the car takes it takes by day; none at night; 80 % by Monday 07:30."""
    result, trail = ev_waits
    assert result.engine_failures == 0
    assert result.deadline_misses == 0
    assert result.ev_soc_at_departure is not None
    assert result.ev_soc_at_departure >= 0.79

    by_day = trail.kwh(trail.ev_w, lambda i: 8 <= _local_hour(trail.at[i]) < 20)
    at_night = trail.kwh(
        trail.ev_w, lambda i: _local_hour(trail.at[i]) >= 22 or _local_hour(trail.at[i]) < 7
    )
    assert by_day > 10.0
    assert at_night == pytest.approx(0.0, abs=0.05)


@pytest.mark.xdist_group(name="phase7_self_consumption")
def test_pv_battery_charges_from_the_surplus_without_importing_for_it(
    self_consumption: tuple[ScenarioResult, _Trail],
) -> None:
    """Grid charging is off: of what the battery takes, at most 10 % coincides with import.

    `min(charge, import)` per tick is what the battery drew that the sun did not
    cover in that tick - the tick-lag of surplus following, when another load
    switches on under it, and nothing the plan asked for.
    """
    result, trail = self_consumption
    assert result.engine_failures == 0
    charged = trail.kwh([max(0.0, w) for w in trail.battery_w])
    from_grid = trail.kwh(
        [
            min(max(0.0, battery), max(0.0, grid))
            for battery, grid in zip(trail.battery_w, trail.grid_w, strict=True)
        ]
    )
    assert charged > 5.0
    assert from_grid <= 0.10 * charged


@pytest.mark.xdist_group(name="phase7_self_consumption")
def test_pv_battery_discharges_into_the_house_s_import_never_into_export(
    self_consumption: tuple[ScenarioResult, _Trail],
) -> None:
    """The evening's discharge displaces import: at most 5 % of it leaves as export."""
    _, trail = self_consumption
    discharged = trail.kwh([max(0.0, -w) for w in trail.battery_w])
    exported = trail.kwh(
        [
            min(max(0.0, -battery), max(0.0, -grid))
            for battery, grid in zip(trail.battery_w, trail.grid_w, strict=True)
        ]
    )
    assert discharged > 2.0
    assert exported <= 0.05 * discharged


@pytest.mark.xdist_group(name="phase7_winter")
def test_pv_battery_peak_shave_winter_holds_the_windows(
    winter: tuple[ScenarioResult, _Trail],
) -> None:
    """A dark January day at 9 kW: no window over, the car on time, the battery used."""
    result, trail = winter
    assert result.engine_failures == 0
    assert result.over_target == 0
    assert result.deadline_misses == 0
    assert result.comfort_violation_min == 0.0
    assert trail.kwh([max(0.0, -w) for w in trail.battery_w]) > 1.0
    assert trail.kwh([max(0.0, w) for w in trail.battery_w]) > 1.0


@pytest.mark.xdist_group(name="phase7_soak")
def test_negative_price_soak_takes_the_surplus_first(
    soak: tuple[ScenarioResult, _Trail],
) -> None:
    """Through the negative hours the tank and the battery soak; export stays under 10 %."""
    result, trail = soak
    assert result.engine_failures == 0
    house = catalogue.negative_price_soak().house()
    negative = {
        slot.start
        for slot in house.prices.slots(catalogue.SOAK_START.date())
        if slot.nok_per_kwh is not None and slot.nok_per_kwh < 0.0
    }
    assert house.production is not None

    def in_negative(index: int) -> bool:
        at = trail.at[index]
        return at.replace(minute=at.minute - at.minute % 15, second=0, microsecond=0) in negative

    produced = trail.kwh([-house.production.at(at) for at in trail.at], in_negative)
    exported = trail.kwh([max(0.0, -grid) for grid in trail.grid_w], in_negative)
    soaked = trail.kwh([max(0.0, w) for w in trail.battery_w], in_negative) + trail.kwh(
        trail.tank_w, in_negative
    )
    assert produced > 5.0
    assert soaked > 5.0
    assert exported <= 0.10 * produced


def _self_consumption(
    trail: _Trail, scenario: str, keep: Callable[[int], bool]
) -> tuple[float, float]:
    """Return `(self-consumed kWh, produced kWh)` over the ticks `keep` selects.

    The production is the scenario's own roof, read off its simulator.
    """
    house = getattr(catalogue, scenario)().house()
    assert house.production is not None
    produced = trail.kwh([-house.production.at(at) for at in trail.at], keep)
    exported = trail.kwh([max(0.0, -grid) for grid in trail.grid_w], keep)
    return max(0.0, produced - exported), produced


@pytest.mark.xdist_group(name="phase7_au_soak")
def test_au_solar_soak_uses_the_surplus_before_the_grid(
    au_soak: tuple[ScenarioResult, _Trail],
) -> None:
    """A Sydney spring Saturday: at least 70 % of the day's production stays at home."""
    result, trail = au_soak
    assert result.engine_failures == 0
    used, produced = _self_consumption(trail, "au_solar_soak", lambda _i: True)
    # Measured 21.8 of 29.0 kWh (75 %): the battery fills by 10:00, the tank and
    # the car soak the midday, and export starts only once all three are full.
    assert produced > 20.0
    assert used >= 0.70 * produced
    assert trail.kwh([max(0.0, w) for w in trail.battery_w]) > 5.0


@pytest.mark.xdist_group(name="phase7_saldering")
def test_nl_saldering_end_self_consumption_rises_from_1_january(
    saldering: tuple[ScenarioResult, _Trail],
) -> None:
    """Net metering gives no reason to shift; its end on 2027-01-01 does: the share used at home rises."""
    result, trail = saldering
    assert result.engine_failures == 0
    zone = catalogue.SALDERING_START.tzinfo

    def before(index: int) -> bool:
        return trail.at[index].astimezone(zone).year == 2026

    def after(index: int) -> bool:
        return not before(index)

    used_before, made_before = _self_consumption(trail, "nl_saldering_end", before)
    used_after, made_after = _self_consumption(trail, "nl_saldering_end", after)
    # Measured 88.8 % across 29–31 December and 94.9 % across 1–3 January.
    assert made_before > 0.0
    assert made_after > 0.0
    assert used_after / made_after > used_before / made_before
