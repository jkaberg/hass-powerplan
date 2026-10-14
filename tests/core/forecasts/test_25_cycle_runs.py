"""A cycle from history (D10 §9 25, D-0752).

Four dishwasher runs of 1.1, 1.2, 1.2 and 1.3 kWh, 2 h each, fit 1.2 kWh and
120 min; two runs do not pass; a 30 min gap mid-run makes two runs, and the
spread fails the gate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custom_components.powerplan.core.forecasts.fit import (
    FitKey,
    LoadHistory,
    cycle_duration_min,
    cycle_energy_kwh,
)

START = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)


def _run(day: int, kwh: float, *, gap_min: int = 0) -> list[tuple[datetime, float]]:
    """Two hours at the power that makes `kwh`, sampled each minute, then off."""
    begin = START + timedelta(days=day)
    watts = kwh * 1000.0 / 2.0
    rows = []
    for minute in range(120):
        at = begin + timedelta(minutes=minute)
        off = gap_min and 50 <= minute < 50 + gap_min
        rows.append((at, 0.0 if off else watts))
    rows.append((begin + timedelta(minutes=120), 0.0))
    return rows


def _history(*runs: list[tuple[datetime, float]]) -> LoadHistory:
    return LoadHistory(
        load_id="dishwasher",
        type_key="appliance_cycle",
        fits=(FitKey.CYCLE_ENERGY, FitKey.CYCLE_DURATION),
        configured={FitKey.CYCLE_ENERGY: 1.0, FitKey.CYCLE_DURATION: 120.0},
        power_rows=tuple(row for run in runs for row in run),
    )


NOW = START + timedelta(days=10)


def test_25_four_runs_fit_their_medians() -> None:
    """1.2 kWh and 120 min, applied."""
    history = _history(_run(0, 1.1), _run(1, 1.2), _run(2, 1.2), _run(3, 1.3))
    energy = cycle_energy_kwh(history, NOW)
    duration = cycle_duration_min(history, NOW)
    assert energy is not None
    assert duration is not None
    assert abs(energy.value - 1.2) < 0.02
    assert duration.value == 120.0
    assert energy.quality.ok
    assert duration.quality.ok


def test_25_two_runs_do_not_pass() -> None:
    """Under three runs: not applied."""
    history = _history(_run(0, 1.1), _run(1, 1.2))
    energy = cycle_energy_kwh(history, NOW)
    assert energy is not None
    assert not energy.quality.ok


def test_25_a_gap_mid_run_splits_it_and_the_spread_fails() -> None:
    """30 min off splits one run in two, and the medians are no longer one program."""
    history = _history(
        _run(0, 1.1, gap_min=30), _run(1, 1.2), _run(2, 1.2, gap_min=30), _run(3, 1.3)
    )
    duration = cycle_duration_min(history, NOW)
    assert duration is not None
    assert duration.quality.n == 6
    assert not duration.quality.ok
    assert "spread" in duration.quality.reason
