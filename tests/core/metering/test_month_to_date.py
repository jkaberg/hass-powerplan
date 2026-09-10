"""D1 §2's month to date, from D3's closed windows (the TS follow-up to D-0610).

The meter keeps what its closed windows imported this local month and adds the
window in progress; a new month starts from its first window. D1 projects it
linearly past now.
"""

from datetime import timedelta

import pytest

from custom_components.powerplan.core.metering import WindowMeter
from custom_components.powerplan.core.pricing.context import month_to_date
from tests.builders import histories
from tests.core.metering.conftest import OSLO, config, drive, local, roundtrip

START_KWH = 42000.0


def test_the_months_closed_windows_and_this_one_are_the_month_to_date() -> None:
    """Three hours at 2 kW and half an hour more: 7 kWh, and it survives the store."""
    start = local(2026, 9, 13, 0, 0)
    trace = histories.constant(start, minutes=210, watts=2000.0, step_s=30.0)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    run = drive(WindowMeter(config(), None), trace, reports)
    now = trace.times[-1]
    assert run.meter.month_to_date_kwh(now) == pytest.approx(7.0, abs=0.1)
    again = WindowMeter(config(), roundtrip(run.meter.state()))
    assert again.month_to_date_kwh(now) == pytest.approx(7.0, abs=0.1)


def test_a_new_month_starts_from_its_first_window() -> None:
    """Across midnight into October, September's windows no longer count."""
    start = local(2026, 9, 30, 22, 0)
    trace = histories.constant(start, minutes=150, watts=1000.0, step_s=30.0)
    reports = histories.latched_reports(
        trace, window_min=60, tz=OSLO, delay_s=12.0, start_kwh=START_KWH
    )
    run = drive(WindowMeter(config(), None), trace, reports)
    assert run.meter.month_to_date_kwh(trace.times[-1]) == pytest.approx(0.5, abs=0.1)


def test_the_projection_runs_at_the_months_mean_rate() -> None:
    """100 kWh by the 10th's midnight is 100/216 kWh an hour: a day later, 111.1 kWh."""
    now = local(2026, 9, 10, 0, 0)
    at = month_to_date(now, OSLO, 100.0)
    assert at(now) == pytest.approx(100.0)
    assert at(now + timedelta(days=1)) == pytest.approx(100.0 + 24 * 100.0 / 216)
    assert at(local(2026, 9, 5, 12, 0)) == pytest.approx(100.0 * 108 / 216)
    assert at(local(2026, 10, 1, 12, 0)) == pytest.approx(12 * 100.0 / 216)
