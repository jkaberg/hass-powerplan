"""D5 §9 20 - a surplus the forecast barely believes is not planned on (D5 §2).

Phase 7. D10's planner view answers the surplus only where the production
forecast's confidence is at least 0.5; below it the slot is priced at `p_in`, so
no deadline waits for sun that may not come. (The replan on a production reading
30 % off its forecast is the runtime's: `tests/runtime/test_pv_forecast.py`.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts.model import (
    SURPLUS_CONFIDENCE,
    ForecastKind,
    Forecasts,
    Series,
    SeriesPoint,
)

NOON = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)


def _forecasts(confidence: float, pv_w: float = 3000.0) -> Forecasts:
    point = SeriesPoint(
        start=NOON, end=NOON + timedelta(hours=1), value=pv_w, confidence=confidence
    )
    series = Series(
        kind=ForecastKind.PRODUCTION, unit="W", points=(point,), source="test", issued_at=NOON
    )
    return Forecasts(at=NOON, production=series)


@pytest.mark.parametrize(
    ("confidence", "surplus"),
    [(0.7, 3000.0), (SURPLUS_CONFIDENCE, 3000.0), (0.49, 0.0), (0.2, 0.0)],
)
def test_20_the_surplus_is_planned_only_at_confidence_0_5_or_more(
    confidence: float, surplus: float
) -> None:
    """0.7 and 0.5 are planned on; 0.49 is not a surplus at all, for the planner."""
    planner = _forecasts(confidence).for_planner()

    assert planner.surplus_w(NOON + timedelta(minutes=30)) == surplus


def test_20_outside_the_forecast_there_is_no_surplus() -> None:
    """A slot no point covers has none: a hole is not sun."""
    planner = _forecasts(0.7).for_planner()

    assert planner.surplus_w(NOON + timedelta(hours=2)) == 0.0
    assert _forecasts(0.7).surplus_naive_w(NOON + timedelta(hours=2)) is None
