"""D9 §5.3's phase-4 rows: `nl_pv_negative_midday`, `be_quarter_hour_rolling`, `zaptec_slow_trim`, `fi_deductible`, `es_contracted_p1_p2`, `us_srp_demand_cooling`.

`nl_pv_negative_midday`: a summer day EPEX NL's own duck-curve shape
(`sim/prices.py`'s `SOLAR_GLUT`, D-0311) goes negative at midday under this
scenario's own seed - confirmed directly against the house's own `PriceSim`,
not asserted blind. The site runs under `nl/connection`'s hard 17.25 kW trip
(D2) with no capacity fee at all: the proof is that the engine never
fails and never averages a window over the trip limit, negative prices or not.

`be_quarter_hour_rolling`: an ordinary January week under Fluvius's capacity
tariff (`be/fluvius`) - quarter-hour windows (four a Tensio hour) and
a `min_kw = 2.5` floor: a window under it bills as if it were exactly there,
checked directly against the loaded preset's own `Linear.billable_kw`.

`zaptec_slow_trim`: the winter week of `reference_winter_day`'s house with the
car behind a Zaptec installation (`tests/sim/charger_zaptec.py`), which may be
raised only once per 15 minutes and may drop a session when told too often
(docs.zaptec.com). The ceiling holds all week: the charger's own reductions are
urgent and pass the interval, and the rest of the house absorbs what the charger
cannot yet take back (D4 §5.9, §5.10).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.powerplan.core.loads import device_types
from custom_components.powerplan.core.model import Carrier
from custom_components.powerplan.providers.profiles import zaptec
from tests.builders.houses import (
    ES_P1_KW,
    ES_P2_KW,
    TEMPO_EUR_PER_KWH,
    ZAPTEC_MIN_INTERVAL_S,
    ZAPTEC_TOLERANCE_A,
    be_quarter,
    fr_tempo,
    nl_pv,
    zaptec_house,
)
from tests.scenarios import catalogue
from tests.scenarios.cache import cached
from tests.scenarios.runner import ZAPTEC_WORDS, _curves, run_scenario
from tests.sim.charger_zaptec import ZaptecChargerSim

if TYPE_CHECKING:
    from tests.scenarios.runner import ScenarioResult

pytestmark = pytest.mark.scenario

#: `nl/connection`'s own trip limit (D2): 3×25 A = 17.25 kW.
NL_TRIP_LIMIT_KW = 17.25
#: A window-average kW is never held to the instant trip check's own zero
#: tolerance (D2 §5.8) - a short overshoot the meter's `tolerance_s` would
#: still forgive can move a window average by a few percent without a real
#: trip; the margin is generous on purpose, and `engine_failures == 0` (the
#: hard test below) covers an actual breach either way.
NL_WINDOW_MARGIN = 1.10


@pytest.fixture(scope="module")
def negative_midday() -> ScenarioResult:
    """Run `nl_pv_negative_midday` once for the module."""
    return cached(
        __file__, "negative_midday", lambda: run_scenario(catalogue.nl_pv_negative_midday())
    )


def test_the_scenario_s_own_day_really_does_go_negative_at_midday() -> None:
    """The proof this scenario needs before it proves anything else (D9 §8)."""
    scenario = catalogue.nl_pv_negative_midday()
    house = nl_pv(start=scenario.start.date())
    trough = scenario.start.replace(hour=12, minute=15, second=0, microsecond=0)
    price = house.prices.at(trough)
    assert price is not None
    assert price < 0.0


@pytest.mark.xdist_group(name="phase4_negative_midday")
def test_the_day_runs_clean_and_never_averages_over_the_trip_limit(
    negative_midday: ScenarioResult,
) -> None:
    """No engine failure, and no window's average power exceeds the hard limit."""
    result = negative_midday
    assert result.engine_failures == 0
    assert result.plan_gaps == 0
    window_h = 1.0  # nl_pv's own `window_min` (site_config's default, 60)
    for kwh in result.window_kwh:
        assert kwh / window_h <= NL_TRIP_LIMIT_KW * NL_WINDOW_MARGIN


@pytest.fixture(scope="module")
def quarter_hour_week() -> ScenarioResult:
    """Run `be_quarter_hour_rolling` once for the module."""
    return cached(
        __file__, "quarter_hour_week", lambda: run_scenario(catalogue.be_quarter_hour_rolling())
    )


@pytest.mark.xdist_group(name="phase4_quarter_hour_week")
def test_the_windows_are_genuinely_quarter_hour(quarter_hour_week: ScenarioResult) -> None:
    """96 windows a day is Fluvius's own 15-minute cadence, not Tensio's 24 hourly ones."""
    result = quarter_hour_week
    scenario = catalogue.be_quarter_hour_rolling()
    expected = round(scenario.days * 24.0 * 60.0 / 15.0)
    assert result.windows == pytest.approx(expected, abs=2)


@pytest.mark.xdist_group(name="phase4_quarter_hour_week")
def test_the_days_run_clean(quarter_hour_week: ScenarioResult) -> None:
    """No engine failure over the two days."""
    result = quarter_hour_week
    assert result.engine_failures == 0
    assert result.plan_gaps == 0


def test_a_quiet_window_still_bills_against_the_floor() -> None:
    """`Linear.billable_kw` (D2 §5.3): under 2.5 kW bills as if it were exactly there."""
    house = be_quarter()
    pricing = house.tariff.spec.versions[0].rules[0].pricing  # type: ignore[union-attr]
    assert pricing.billable_kw(1.0) == pricing.min_kw == 2.5
    assert pricing.billable_kw(4.0) == 4.0


def test_the_negative_midday_day_is_a_real_regression_guard() -> None:
    """If `SOLAR_GLUT`'s tuning or the house's default seed ever moves, this fails loudly.

    Rather than the scenario quietly testing nothing (D9 §8: a flaky or a
    toothless scenario is a bug).
    """
    scenario = catalogue.nl_pv_negative_midday()
    house = nl_pv(start=scenario.start.date())
    day = scenario.start.date()
    prices = (
        house.prices.at(datetime(day.year, day.month, day.day, hour, 15, tzinfo=house.cfg.tz))
        for hour in range(24)
    )
    worst = min(price for price in prices if price is not None)
    assert worst < 0.0


# --------------------------------------------------------------------------- #
# `zaptec_slow_trim`
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def zaptec_week() -> ScenarioResult:
    """Run `zaptec_slow_trim` once for the module."""
    return cached(__file__, "zaptec_week", lambda: run_scenario(catalogue.zaptec_slow_trim()))


def test_the_zaptec_house_is_steered_the_way_the_profile_says() -> None:
    """The builder's charger is the `zaptec` profile's: its gate row and its words (D4 §5.9).

    The runner and the builder write both out because the simulation cache keys on
    `tests/` and `core/`, not on `providers/`; this is where they are held to the
    profile.
    """
    house = zaptec_house()
    load = house.load("ev")
    kind_gate = device_types.get("ev").build(load.config).gate

    assert load.gate == zaptec.QUIRKS.raised(kind_gate)
    assert (
        zaptec.QUIRKS.tolerance,
        zaptec.QUIRKS.min_interval_s,
    ) == (ZAPTEC_TOLERANCE_A, ZAPTEC_MIN_INTERVAL_S)
    assert load.config.params["limit_pauses"] is True
    assert {
        status: zaptec.STATUSES.state(status).status_word for status in zaptec.STATUSES.options
    } == ZAPTEC_WORDS
    assert isinstance(house.sims["ev"], ZaptecChargerSim)


@pytest.mark.xdist_group(name="phase4_zaptec_week")
def test_zaptec_slow_trim_holds_the_ceiling_all_week(zaptec_week: ScenarioResult) -> None:
    """`over_target` = 0 through the winter week, and the engine never fails (D9 §5.3)."""
    result = zaptec_week

    assert result.windows >= 7 * 24 - 1
    assert result.over_target == 0
    assert result.engine_failures == 0
    assert result.plan_gaps == 0


@pytest.mark.xdist_group(name="phase4_zaptec_week")
def test_zaptec_slow_trim_never_raises_twice_in_fifteen_minutes(
    zaptec_week: ScenarioResult,
) -> None:
    """At most one non-urgent write per 900 s - every change inside the window is a trim."""
    assert zaptec_week.house is not None
    charger = zaptec_week.house.sims["ev"]
    assert isinstance(charger, ZaptecChargerSim)

    assert charger.changes > 0, "the charger was steered at all"
    assert charger.raises_too_soon == 0
    for (earlier, *_), (later, before, after) in zip(charger.log, charger.log[1:], strict=False):
        if (later - earlier).total_seconds() < ZAPTEC_MIN_INTERVAL_S:
            assert after < before, f"a raise {before} → {after} A at {later} inside 15 minutes"


@pytest.mark.xdist_group(name="phase4_zaptec_week")
def test_zaptec_slow_trim_trims_urgently_when_the_ceiling_needs_it(
    zaptec_week: ScenarioResult,
) -> None:
    """The urgent path is real, not vacuous: some trims landed inside the 15 minutes."""
    assert zaptec_week.house is not None
    charger = zaptec_week.house.sims["ev"]
    assert isinstance(charger, ZaptecChargerSim)

    assert charger.changes_too_soon > 0
    assert charger.changes_too_soon == sum(
        1
        for (earlier, *_), (later, before, after) in zip(charger.log, charger.log[1:], strict=False)
        if (later - earlier).total_seconds() < ZAPTEC_MIN_INTERVAL_S and after < before
    )


# --------------------------------------------------------------------------- #
# WP4.3b: fi_deductible, es_contracted_p1_p2, and fr_tempo's announcements
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def tehomaksu_days() -> ScenarioResult:
    """Run `fi_deductible` once for the module."""
    return cached(__file__, "tehomaksu_days", lambda: run_scenario(catalogue.fi_deductible()))


@pytest.mark.xdist_group(name="phase4_tehomaksu_days")
def test_fi_deductible_keeps_every_hour_inside_the_free_8_kw(
    tehomaksu_days: ScenarioResult,
) -> None:
    """D9 §5.3: the peak kept ≤ 8 kW when cheap to do so - two January days, no fee paid."""
    result = tehomaksu_days
    assert result.engine_failures == 0
    assert result.plan_gaps == 0
    assert result.over_target == 0
    assert result.max_window_kwh <= catalogue.FI_FREE_KW


@pytest.fixture(scope="module")
def contracted_days() -> ScenarioResult:
    """Run `es_contracted_p1_p2` once for the module."""
    return cached(
        __file__, "contracted_days", lambda: run_scenario(catalogue.es_contracted_p1_p2())
    )


def _p1(start: str) -> bool:
    """2.0TD's P1: working weekdays 08:00–24:00 in Madrid (the rule's own `when`)."""
    local = datetime.fromisoformat(start).astimezone(catalogue.ES_P1_P2_START.tzinfo)
    return local.weekday() < 5 and local.hour >= 8


@pytest.mark.xdist_group(name="phase4_contracted_days")
def test_es_contracted_never_trips_and_uses_p2_at_night(contracted_days: ScenarioResult) -> None:
    """D9 §5.3: no hour over its period's contracted power, and P2's extra room is used."""
    result = contracted_days
    assert result.engine_failures == 0
    by_period = [
        (_p1(start), kwh)
        for start, kwh in zip(result.window_starts, result.window_kwh, strict=True)
    ]
    assert all(kwh <= (ES_P1_KW if p1 else ES_P2_KW) for p1, kwh in by_period)
    assert max(kwh for p1, kwh in by_period if not p1) > ES_P1_KW


def test_fr_tempo_prices_tomorrow_only_once_its_colour_is_announced() -> None:
    """The red day's peak hours cost 0.7295 once announced; before 10:40 they are blue."""
    house = fr_tempo()
    assert house.announcer is not None
    red = next(
        day
        for day in (datetime(2027, 1, 4, tzinfo=UTC).date() + timedelta(days=n) for n in range(60))
        if house.announcer.colour(day) == "red"
    )
    noon = datetime.combine(red, time(12), tzinfo=house.cfg.tz)
    before = datetime.combine(red - timedelta(days=1), time(10, 30), tzinfo=house.cfg.tz)
    after = datetime.combine(red - timedelta(days=1), time(10, 50), tzinfo=house.cfg.tz)

    def price(now: datetime) -> float:
        curve = _curves(house, now.astimezone(UTC)).import_[Carrier.ELECTRICITY]
        slot = next(slot for slot in curve.slots if slot.start <= noon < slot.end)
        return float(slot.total)

    assert price(before) == TEMPO_EUR_PER_KWH["blue"][1]
    assert price(after) == TEMPO_EUR_PER_KWH["red"][1]


# --------------------------------------------------------------------------- #
# WP4.3c: us_srp_demand_cooling
# --------------------------------------------------------------------------- #

#: SRP's summer on-peak, local: 14:00–20:00 on weekdays.
ON_PEAK_FROM_H = 14
ON_PEAK_TO_H = 20
#: The morning the unit may pre-cool in, before the window.
BEFORE_FROM_H = 10


class _CoolingTrail:
    """The heat pump's measured draw per tick, keyed by local hour (picklable)."""

    def __init__(self) -> None:
        self.wh_by_hour: dict[tuple[str, int], float] = {}
        self._last: datetime | None = None

    def __call__(self, now: datetime, snapshot: Any) -> None:
        local = now.astimezone(catalogue.US_DEMAND_START.tzinfo)
        status = snapshot.loads["heat_pump"]
        if self._last is not None and status.measured_w is not None:
            seconds = (now - self._last).total_seconds()
            key = (local.date().isoformat(), local.hour)
            self.wh_by_hour[key] = (
                self.wh_by_hour.get(key, 0.0) + status.measured_w * seconds / 3600
            )
        self._last = now


@pytest.fixture(scope="module")
def cooling_days() -> tuple[ScenarioResult, _CoolingTrail]:
    """Run `us_srp_demand_cooling` once for the module, keeping the heat pump's draw."""

    def run() -> tuple[ScenarioResult, _CoolingTrail]:
        trail = _CoolingTrail()
        return run_scenario(catalogue.us_srp_demand_cooling(), trail), trail

    return cached(__file__, "cooling_days", run)


@pytest.mark.xdist_group(name="phase4_cooling_days")
def test_us_srp_on_peak_demand_stays_at_or_under_the_target(
    cooling_days: tuple[ScenarioResult, _CoolingTrail],
) -> None:
    """D9 §5.3: every 30-minute on-peak window's average at or under 5 kW."""
    result, _ = cooling_days
    assert result.engine_failures == 0
    zone = catalogue.US_DEMAND_START.tzinfo
    for start, kwh in zip(result.window_starts, result.window_kwh, strict=True):
        local = datetime.fromisoformat(start).astimezone(zone)
        if local.weekday() < 5 and ON_PEAK_FROM_H <= local.hour < ON_PEAK_TO_H:
            assert kwh * 2.0 <= catalogue.US_DEMAND_TARGET_KW, (start, kwh)


@pytest.mark.xdist_group(name="phase4_cooling_days")
def test_us_srp_the_heat_pump_cools_before_the_window_and_coasts_through_it(
    cooling_days: tuple[ScenarioResult, _CoolingTrail],
) -> None:
    """D9 §5.3's pre-cooling: more cooling energy 10–14 than 14–20, on each weekday."""
    _, trail = cooling_days
    days = sorted({day for day, _ in trail.wh_by_hour})
    assert days
    for day in days:
        before = sum(
            trail.wh_by_hour.get((day, hour), 0.0) for hour in range(BEFORE_FROM_H, ON_PEAK_FROM_H)
        )
        during = sum(
            trail.wh_by_hour.get((day, hour), 0.0) for hour in range(ON_PEAK_FROM_H, ON_PEAK_TO_H)
        )
        if before or during:
            assert before > during, (day, before, during)
