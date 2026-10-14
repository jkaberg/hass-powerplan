"""The held setpoint (D10 §9 27, D-0752, INV-27).

22.0 °C for 10 of 14 days and 18.0 for 4 fits 22.0; 22.0 for 6 days and 21.0
for 8 fits 21.0; a third each of 21, 22 and 23 fits nothing; a device powerplan
has written to fits nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.powerplan.core.forecasts.fit import FitKey, LoadHistory, held_setpoint

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _history(*spans: tuple[float, float], written: bool = False) -> LoadHistory:
    """`(setpoint, days)` back to back, ending now."""
    total = sum(days for _value, days in spans)
    at = NOW - timedelta(days=total)
    rows = []
    for value, days in spans:
        rows.append((at, value))
        at += timedelta(days=days)
    return LoadHistory(
        load_id="floor",
        type_key="floor_heating",
        fits=(FitKey.COMFORT,),
        setpoint_rows=tuple(rows),
        written=written,
    )


@pytest.mark.inv("INV-27")
def test_27_ten_days_of_fourteen_fit_their_value() -> None:
    """22.0 held longest, over half the window."""
    fit = held_setpoint(_history((18.0, 4.0), (22.0, 10.0)), NOW)
    assert fit is not None
    assert (fit.value, fit.quality.ok) == (22.0, True)


def test_27_the_longest_held_wins() -> None:
    """21.0 for 8 days beats 22.0 for 6."""
    fit = held_setpoint(_history((22.0, 6.0), (21.0, 8.0)), NOW)
    assert fit is not None
    assert (fit.value, fit.quality.ok) == (21.0, True)


def test_27_three_thirds_fit_nothing() -> None:
    """No value held half the window."""
    fit = held_setpoint(_history((21.0, 14 / 3), (22.0, 14 / 3), (23.0, 14 / 3)), NOW)
    assert fit is not None
    assert not fit.quality.ok


@pytest.mark.inv("INV-27")
def test_27_a_device_powerplan_wrote_to_fits_nothing() -> None:
    """Its setpoint history is powerplan's own targets."""
    assert held_setpoint(_history((22.0, 14.0), written=True), NOW) is None
