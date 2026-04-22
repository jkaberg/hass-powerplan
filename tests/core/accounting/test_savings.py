"""D11 §9 10, 11, 13 - the site identity, the capacity step, and calibration.

Three claims the savings figure rests on:

* the site's savings are **Σ the loads' energy shift + (cf fee − fee)**, and
  uncontrolled load cancels out of it exactly - it is identical in both worlds, so
  a house that used 40 kWh of it moves neither figure (D11 §5.4);
* the capacity component is a difference of two bills from one evaluator (INV-52);
* the number carries a confidence, and the confidence comes from observe days
  measured against the simulator - never from a parameter this module changed
  (INV-63).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.accounting import (
    CALIBRATION_THRESHOLD,
    MIN_OBSERVE_DAYS,
    LoadParams,
    SavingsConfidence,
    StoreKind,
)
from custom_components.powerplan.core.accounting.ledger import minus, plus
from custom_components.powerplan.core.loads.stores import SlabStore
from custom_components.powerplan.core.model import Mode
from tests.core.accounting.conftest import (
    NOK,
    ORDINARY,
    OSLO,
    closed_slot,
    config,
    curve,
    demand,
    flat,
    local,
    shadow_ctx,
    site,
    tensio,
    window,
)
from tests.sim.slab import SlabSim
from tests.sim.weather import WeatherSim

MAX_W = 11000.0
STEP_S = 60.0

#: Tensio's 2026 household table (D2 §6, `tests/core/tariffs/conftest.py`):
#: 2–5 kW → 244 NOK, 5–10 kW → 416 NOK, 10–15 kW → 613 NOK.
STEP_2_5 = Decimal(244)
STEP_5_10 = Decimal(416)
STEP_10_15 = Decimal(613)


def _ev_ctx(required_kwh: float | None = 4.0, *, wants: bool = True) -> object:
    return shadow_ctx(
        params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W),
        demand=demand(wants=wants, required_kwh=required_kwh, max_w=MAX_W),
    )


# --------------------------------------------------------------------------- #
# 10 - the site identity
# --------------------------------------------------------------------------- #


def test_10_the_sites_savings_are_the_loads_energy_shift_plus_the_capacity_part() -> None:
    """The identity D8 publishes both halves of (D11 §4)."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("ev", _ev_ctx(20.0))

    # Plugged in at 17:00 with 40 kWh of unrelated house load in the same hours.
    for hour in (17, 18, 19, 22, 23):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=20.0, max_w=MAX_W))
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"ev": 11.0 if hour == 22 else (9.0 if hour == 23 else 0.0)},
                uncontrolled_kwh=8.0,
            )
        )

    status = under_test.accounting.status()
    loads_savings = status.loads["ev"].savings
    assert status.site.energy_savings == loads_savings
    assert status.site.savings == plus(loads_savings, status.site.capacity_savings)
    assert status.site.capacity_savings.amount == 0, "a NoPeak site has no capacity axis"


def test_10b_uncontrolled_load_cancels_out_of_the_site_figure_exactly() -> None:
    """Forty kilowatt-hours of unrelated house load moves neither figure (§5.4)."""
    quiet = site(import_curve=curve(ORDINARY, days=2))
    busy = site(import_curve=curve(ORDINARY, days=2))
    for under_test, uncontrolled in ((quiet, 0.0), (busy, 8.0)):
        under_test.with_load("ev", _ev_ctx(20.0))
        for hour in (17, 18, 22, 23):
            under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=20.0, max_w=MAX_W))
            under_test.close(
                closed_slot(
                    local(2026, 12, 3, hour, 0).astimezone(UTC),
                    loads={"ev": 11.0 if hour == 22 else (9.0 if hour == 23 else 0.0)},
                    uncontrolled_kwh=uncontrolled,
                )
            )

    assert quiet.accounting.status().site.savings == busy.accounting.status().site.savings
    # The difference of the two site energy figures is the loads' and nothing else.
    for under_test in (quiet, busy):
        status = under_test.accounting.status()
        rec = under_test.accounting.state().ledger.site
        assert minus(rec.cf_energy_cost, rec.energy_cost) == status.loads["ev"].savings


def test_10c_a_flat_curve_leaves_a_completed_session_with_no_energy_savings() -> None:
    """Under Norgespris the energy figure reads 0 by construction (D11 §5.1)."""
    under_test = site(import_curve=curve(ORDINARY, days=2, shape=flat))
    under_test.with_load("ev", _ev_ctx(20.0))
    for hour in (17, 18, 19, 22, 23):
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=20.0, max_w=MAX_W))
        under_test.close(
            closed_slot(
                local(2026, 12, 3, hour, 0).astimezone(UTC),
                loads={"ev": 11.0 if hour == 22 else (9.0 if hour == 23 else 0.0)},
            )
        )

    rec = under_test.accounting.state().ledger.loads["ev"]
    assert rec.kwh == pytest.approx(rec.cf_kwh), "the same energy in both worlds"
    assert rec.savings.amount == 0, "and the same price for every hour of it"
    assert rec.kwh_shifted == pytest.approx(20.0), "it still ran at a different time"


# --------------------------------------------------------------------------- #
# 11 - the capacity step
# --------------------------------------------------------------------------- #


def test_11_the_ev_moved_off_the_evening_peak_is_one_capacity_step() -> None:
    """NO preset: a cf daily max of 8.5 kW against an actual 4.5 kW (D11 §5.4).

    §9 11 names 9 kW against 5 kW, but D2's step thresholds are inclusive upward
    (D2 §4 `StepTable`), so 5.00 kW is already the 5–10 kW step and both worlds
    would land in it. 4.5 kW of house plus the car's 4 kWh is the same statement
    one step lower, and it is the pair the D2 §9 18 golden uses.
    """
    under_test = site(import_curve=curve(date(2026, 12, 1), days=8), tariff=tensio())
    under_test.with_load("ev", _ev_ctx(4.0))

    for day in (3, 4, 5):
        # 18:00: the house draws 4.5 kWh and the car is plugged in wanting 4 kWh.
        under_test.ctx_for("ev", demand=demand(wants=True, required_kwh=4.0, max_w=MAX_W))
        evening = local(2026, 12, day, 18, 0).astimezone(UTC)
        # The *real* window is D7's to record: the accounting writes the shadow book
        # beside the real history and never into it (INV-11, INV-68).
        under_test.tariff.record_window(window(evening, 4.5))
        under_test.close(
            closed_slot(
                evening,
                loads={"ev": 0.0},
                uncontrolled_kwh=4.5,
                window_closed=window(evening, 4.5),
            )
        )
        # 02:00 the next morning: powerplan charges the 4 kWh.
        night = local(2026, 12, day + 1, 2, 0).astimezone(UTC)
        under_test.tariff.record_window(window(night, 4.0))
        under_test.close(closed_slot(night, loads={"ev": 4.0}, window_closed=window(night, 4.0)))
        # The car unplugs, so the next evening is a new session. D3 closes a slot
        # for every load every slot, idle or not, which is what steps the shadow.
        under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
        under_test.close(
            closed_slot(local(2026, 12, day + 1, 7, 0).astimezone(UTC), loads={"ev": 0.0})
        )

    rec = under_test.accounting.state().ledger.site
    assert rec.windows_cf == 6, "every slot that completed a window recorded a shadow one"

    period = under_test.tariff.period(local(2026, 12, 20, 12, 0))
    actual = under_test.tariff.bill(period, under_test.tariff.history)
    cf = under_test.tariff.bill(period, under_test.tariff.history.counterfactual())
    assert actual.metric_kw == pytest.approx(4.5), "the evening window, three days running"
    assert cf.metric_kw == pytest.approx(8.5), "4.5 kWh of house plus 4 kWh of car"
    assert actual.level.name == "2–5 kW"
    assert cf.level.name == "5–10 kW"

    # The month's share is the whole fee: this is the site's first month (D11 §5.1).
    assert rec.capacity_fee.amount == STEP_2_5
    assert rec.cf_capacity_fee.amount == STEP_5_10
    assert rec.capacity_savings.amount == STEP_5_10 - STEP_2_5

    status = under_test.accounting.status()
    assert status.site.capacity_savings.amount == Decimal(172)
    assert status.site.savings == plus(status.loads["ev"].savings, status.site.capacity_savings)


def test_11b_a_nopeak_site_records_no_shadow_window_and_bills_no_capacity() -> None:
    """DK, UK, DE: the site degrades to pure price steering (D2 §2)."""
    under_test = site(import_curve=curve(ORDINARY, days=2))
    under_test.with_load("ev", _ev_ctx(4.0))
    start = local(2026, 12, 3, 18, 0).astimezone(UTC)
    under_test.close(
        closed_slot(
            start, loads={"ev": 0.0}, uncontrolled_kwh=5.0, window_closed=window(start, 5.0)
        )
    )

    rec = under_test.accounting.state().ledger.site
    assert rec.capacity_fee.amount == 0
    assert rec.cf_capacity_fee.amount == 0
    assert not under_test.tariff.history.counterfactual_days, "nothing to record"


# --------------------------------------------------------------------------- #
# 13 - calibration
# --------------------------------------------------------------------------- #

CALIB_FIRST = date(2026, 12, 3)
CALIB_DAYS = 5
AREA_M2 = 20.0


def _slab_params(ua_w_per_k: float) -> LoadParams:
    return LoadParams(
        kind=StoreKind.SLAB,
        nameplate_w=AREA_M2 * 80.0,
        store=SlabStore(area_m2=AREA_M2, screed_mm=50.0, loss_coeff_w_per_k=ua_w_per_k, max_c=27.0),
        band_k=1.0,
        loss_coeff_w_per_k=ua_w_per_k,
    )


def _observe_days(ua_w_per_k: float, days: int = CALIB_DAYS):
    """Run the slab simulator uncontrolled and account every hour as an observe slot.

    A load in `observe` behaves exactly as its counterfactual predicts, so over
    these slots `cf_kwh − kwh` is pure model error (D11 §2, §5.5).
    """
    weather = WeatherSim(seed=7)
    sim = SlabSim(area_m2=AREA_M2, setpoint_c=22.0, screed_c=22.0, room_c=21.0)
    under_test = site(import_curve=curve(CALIB_FIRST, days=days + 1), cfg=config(currency=NOK))
    under_test.with_load(
        "floor",
        shadow_ctx(params=_slab_params(ua_w_per_k), mode=Mode.OBSERVE, target=22.0, level_now=22.0),
    )

    cursor = datetime.combine(CALIB_FIRST, datetime.min.time(), tzinfo=OSLO).astimezone(UTC)
    for _ in range(days * 24):
        before = sim.energy_in_kwh
        outdoor = weather.env_at(cursor).outdoor_c
        for _ in range(int(3600 / STEP_S)):
            sim.step(STEP_S, None, weather.env_at(cursor))
        under_test.ctx_for("floor", outdoor_c=outdoor, level_now=sim.screed_c)
        under_test.close(closed_slot(cursor, loads={"floor": sim.energy_in_kwh - before}))
        cursor += timedelta(hours=1)
    return under_test


@pytest.mark.inv("INV-63")
def test_13_five_observe_days_against_the_simulator_calibrate_ok() -> None:
    """The error is published, and under the threshold it reads `ok` (D11 §5.5)."""
    weather = WeatherSim(seed=7)
    sim = SlabSim(area_m2=AREA_M2, setpoint_c=22.0, screed_c=22.0, room_c=21.0)
    cursor = datetime.combine(CALIB_FIRST, datetime.min.time(), tzinfo=OSLO).astimezone(UTC)
    powers, diffs = [], []
    for _ in range(24 * 60):
        env = weather.env_at(cursor)
        powers.append(sim.step(STEP_S, None, env).power_w)
        diffs.append(sim.screed_c - env.outdoor_c)
        cursor += timedelta(seconds=STEP_S)
    ua = (sum(powers) / len(powers)) / (sum(diffs) / len(diffs))

    under_test = _observe_days(ua)
    figures = under_test.accounting.status().loads["floor"]

    assert figures.calibration_error is not None
    assert figures.calibration_error < 0.10, figures.calibration_error
    assert figures.savings_confidence is SavingsConfidence.OK
    calib = under_test.accounting.state().calibration["floor"]
    assert calib.observe_days == CALIB_DAYS


@pytest.mark.inv("INV-63")
def test_13b_a_loss_coefficient_off_by_two_reads_low_and_changes_nothing() -> None:
    """Calibration is a report. It never writes a parameter (D11 §2, INV-63)."""
    under_test = _observe_days(24.0)  # roughly twice the fitted coefficient
    figures = under_test.accounting.status().loads["floor"]

    assert figures.calibration_error is not None
    assert figures.calibration_error > CALIBRATION_THRESHOLD
    assert figures.savings_confidence is SavingsConfidence.LOW
    # The parameter the shadow read is the parameter the load still has.
    assert under_test.loads["floor"].params.loss_coeff_w_per_k == 24.0


def test_13c_fewer_than_three_observe_days_is_uncalibrated() -> None:
    """Three days before the counterfactual is trusted (PLAN §7 dec. 12)."""
    under_test = _observe_days(12.0, days=MIN_OBSERVE_DAYS - 1)
    figures = under_test.accounting.status().loads["floor"]

    assert figures.savings_confidence is SavingsConfidence.UNCALIBRATED
    assert figures.calibration_error is not None, "the error is still published"


def test_13d_the_sites_confidence_is_the_worst_of_the_loads_that_move_it() -> None:
    """A load whose savings are noise beside the site's does not set its confidence."""
    under_test = _observe_days(24.0)
    status = under_test.accounting.status()

    assert status.loads["floor"].savings_confidence is SavingsConfidence.LOW
    assert status.site.savings_confidence is SavingsConfidence.LOW
