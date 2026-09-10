"""D10 §9 15 - the planner's view of the forecasts (D5 `PlanContext`, D10 §3).

The added §9 item, and it exists because the code forced it: D5's
`strategies/context.Forecasts` protocol asks three questions and takes
bare floats - `outdoor_c`, `surplus_w`, `baseline_w` - while D10 §3's `Forecasts`
answers `(value, Confidence) | None` so a consumer can see how much a number is
worth. `for_planner()` is the one-way bridge between them, and the rule it
carries is INV-62's: a baseline that is not offered subtracts **nothing** from
the planner's headroom, rather than subtracting a guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.powerplan.core.forecasts import ForecastKind, Forecasts, Series, SeriesPoint
from custom_components.powerplan.core.model import Confidence, Slot
from custom_components.powerplan.core.strategies import Headroom
from tests.core.forecasts.conftest import NOW, seeded

MEAN_W = 1200.0
CEILING_W = 5000.0
OUTDOOR_C = -7.5


class _Ceiling:
    """The narrow `CeilingSource` D5 reads a flat target through (D5 §5.1)."""

    def target_w_at(self, t: datetime, target: object) -> float:
        """Return the same ceiling for every window."""
        return CEILING_W

    def priced_limit_now(self, now: datetime) -> None:
        """Return no priced limit: this tariff has none."""
        return

    def eligible_windows(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime, float]]:
        """Return no eligible windows; the headroom test does not read them."""
        return []


def _weather() -> Series:
    """Return an hourly outdoor-temperature series over the next six hours."""
    return Series(
        kind=ForecastKind.WEATHER,
        unit="°C",
        points=tuple(
            SeriesPoint(
                start=NOW + timedelta(hours=hour),
                end=NOW + timedelta(hours=hour + 1),
                value=OUTDOOR_C,
                confidence=0.9,
            )
            for hour in range(6)
        ),
        source="test_weather",
        issued_at=NOW,
    )


def _slots() -> tuple[Slot, ...]:
    """One quarter-hour price slot at `NOW`, the shape `Headroom.build` takes."""
    return (
        Slot(
            start=NOW,
            end=NOW + timedelta(minutes=15),
            total=Decimal("1.00"),
            components={"spot": Decimal("1.00")},
            confidence=Confidence.KNOWN,
        ),
    )


def test_15_the_planner_view_answers_d5s_three_questions() -> None:
    """Bare floats, as `PlanContext` reads them (D5 §5.1)."""
    view = Forecasts(at=NOW, weather=_weather(), baseline=seeded(8.0, MEAN_W)).for_planner()

    assert view.outdoor_c(NOW) == pytest.approx(OUTDOOR_C)
    assert view.baseline_w(NOW) == pytest.approx(MEAN_W)
    assert view.surplus_w(NOW) == 0.0


def test_15b_an_unoffered_baseline_subtracts_nothing() -> None:
    """Warm-up leaves the headroom alone instead of guessing at it (INV-62)."""
    view = Forecasts(at=NOW, baseline=seeded(3.0, MEAN_W)).for_planner()

    assert view.baseline_w(NOW) == 0.0
    assert view.outdoor_c(NOW) is None


def test_15c_the_headroom_the_strategies_build_uses_it() -> None:
    """The bridge really is what D5 takes: one slot, one ceiling, one baseline."""
    slots = _slots()
    offered = Forecasts(at=NOW, baseline=seeded(8.0, MEAN_W)).for_planner()
    warming = Forecasts(at=NOW, baseline=seeded(3.0, MEAN_W)).for_planner()

    with_baseline = Headroom.build(slots, tariff=_Ceiling(), forecasts=offered)
    without = Headroom.build(slots, tariff=_Ceiling(), forecasts=warming)

    assert with_baseline.w_at(slots[0].start) == pytest.approx(CEILING_W - MEAN_W)
    assert without.w_at(slots[0].start) == pytest.approx(CEILING_W)
