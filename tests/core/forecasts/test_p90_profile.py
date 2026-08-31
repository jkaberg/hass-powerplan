"""The rest of the house's hour-of-week P90 (D12 §5.12 F2, D-0498)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.forecasts.quantiles import (
    MIN_SAMPLES,
    HourOfWeekQuantile,
    quantile,
)
from custom_components.powerplan.core.forecasts.reconstruct import (
    UncontrolledHistory,
    UncontrolledWindow,
)
from custom_components.powerplan.core.metering import AnchorKind, ClosedWindow

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 9, 23, 22, 0, tzinfo=UTC)


def _history(values: dict[datetime, float]) -> UncontrolledHistory:
    return UncontrolledHistory(
        windows=tuple(
            UncontrolledWindow(
                window=ClosedWindow(
                    start_utc=start,
                    window_min=60,
                    kwh=kwh,
                    avg_kw=kwh,
                    anchor_kind=AnchorKind.REGISTER_LATCHED,
                    degraded=False,
                    confidence="exact",
                ),
                uncontrolled_kwh=kwh,
                controlled_kwh=0.0,
            )
            for start, kwh in values.items()
        )
    )


def test_quantile_interpolates() -> None:
    """Linear between order statistics."""
    assert quantile([1.0, 2.0, 3.0, 4.0, 5.0], 0.9) == pytest.approx(4.6)
    assert quantile([2.0], 0.9) == 2.0


def test_the_week_hour_s_p90_and_the_day_hour_fallback() -> None:
    """Four Wednesdays at 00–01 give that week-hour its own P90; a lone Thursday falls back."""
    wednesday = datetime(2026, 9, 22, 22, 0, tzinfo=UTC)  # Wed 00:00 in Oslo
    values = {wednesday - timedelta(days=7 * i): kwh for i, kwh in enumerate((1.2, 1.4, 1.6, 3.0))}
    values[wednesday + timedelta(days=1)] = 0.5  # one Thursday 00–01
    values[wednesday - timedelta(days=40)] = 9.0  # outside the lookback
    values[wednesday + timedelta(hours=5)] = 45.0  # a meter glitch
    profile = HourOfWeekQuantile.from_history(_history(values), OSLO, NOW)
    assert profile is not None
    assert len([v for v in values if v > NOW - timedelta(days=28)]) > MIN_SAMPLES
    assert profile.kwh_per_hour(wednesday) == pytest.approx(quantile([1.2, 1.4, 1.6, 3.0], 0.9))
    thursday = wednesday + timedelta(days=1)
    assert profile.kwh_per_hour(thursday) == pytest.approx(quantile([1.2, 1.4, 1.6, 3.0, 0.5], 0.9))
    assert profile.kwh_per_hour(wednesday + timedelta(hours=5)) is None
    # A quarter-hour slot is a quarter of its hour; a slot over an empty hour has none.
    assert profile.kwh_between(wednesday, wednesday + timedelta(minutes=15)) == pytest.approx(
        profile.kwh_per_hour(wednesday) / 4
    )
    assert profile.kwh_between(wednesday, wednesday + timedelta(hours=6)) is None


def test_no_usable_window_is_no_profile() -> None:
    """No history: no profile, and the slots fall back to D10's σ (D-0494)."""
    assert HourOfWeekQuantile.from_history(UncontrolledHistory(), OSLO, NOW) is None


def test_quarter_hour_windows_are_summed_into_their_hour_first() -> None:
    """The seed's recent days come as quarters (D-0505): 4 × 0.3 kWh is a 1.2 kWh hour, not 0.3 × 4 rates."""
    wednesday = datetime(2026, 9, 22, 22, 0, tzinfo=UTC)  # Wed 00:00 in Oslo
    windows = []
    for week in range(3):
        for quarter in range(4):
            start = wednesday - timedelta(days=7 * week) + timedelta(minutes=15 * quarter)
            windows.append(
                UncontrolledWindow(
                    window=ClosedWindow(
                        start_utc=start, window_min=15, kwh=0.3, avg_kw=1.2,
                        anchor_kind=AnchorKind.REGISTER_LATCHED, degraded=False, confidence="exact",
                    ),
                    uncontrolled_kwh=0.3,
                    controlled_kwh=0.0,
                )
            )  # fmt: skip
    # A lone quarter of the next hour is not a whole hour: left out.
    windows.append(
        UncontrolledWindow(
            window=ClosedWindow(
                start_utc=wednesday + timedelta(hours=1), window_min=15, kwh=0.3, avg_kw=1.2,
                anchor_kind=AnchorKind.REGISTER_LATCHED, degraded=False, confidence="exact",
            ),
            uncontrolled_kwh=0.3,
            controlled_kwh=0.0,
        )
    )  # fmt: skip
    profile = HourOfWeekQuantile.from_history(
        UncontrolledHistory(windows=tuple(windows)), OSLO, NOW
    )
    assert profile is not None
    assert profile.kwh_per_hour(wednesday) == pytest.approx(1.2)
    assert profile.kwh_per_hour(wednesday + timedelta(hours=1)) is None
