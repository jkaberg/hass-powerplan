"""D12 §9 24 - the baseline: an old water-heater timer must not leak into "other usage" (D-0584).

On the integration's `uncontrolled_history`. The house uses ≈ 1 kWh an hour; the water heater
adds 3,5 kWh at 22:00 and 23:00 local every night. On the reference house the meter's hourly
statistics ran one hour behind the heater's own meter, and a load whose history began later than
the meter's leaked its older runs into "other usage".
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta

from custom_components.powerplan.core.forecasts import ControlledHistory, uncontrolled_history
from tests.core.forecasts.conftest import OSLO

START = datetime(2026, 8, 27, 0, tzinfo=OSLO)
DAYS = 28
HEATER_KWH = 3.5


def synth(
    meter_lag_h: int, days: int = DAYS, seed: int = 1
) -> tuple[list[tuple[datetime, float]], ControlledHistory]:
    """Return the meter's cumulative register rows and the heater's own power history."""
    rnd = random.Random(seed)
    hourly: dict[datetime, float] = defaultdict(float)
    power: list[tuple[datetime, float]] = []
    for h in range(days * 24):
        t = START + timedelta(hours=h)
        heater = HEATER_KWH if t.hour in (22, 23) else 0.0
        # A mean-per-hour step, as `async_load_power_w` returns it.
        power += [(t, heater * 1000.0), (t + timedelta(hours=1), heater * 1000.0)]
        hourly[t + timedelta(hours=meter_lag_h)] += 1.0 + rnd.uniform(-0.1, 0.1) + heater
    rows: list[tuple[datetime, float]] = []
    total = 0.0
    for t in sorted(hourly):
        rows.append((t, total))
        total += hourly[t]
    rows.append((max(hourly) + timedelta(hours=1), total))
    return rows, ControlledHistory(load_id="vvb", nameplate_w=3500.0, power_rows=tuple(power))


def by_local_hour(history) -> dict[int, float]:
    """Mean uncontrolled kWh per local hour of day."""
    sums: dict[int, list[float]] = defaultdict(list)
    for row in history.windows:
        sums[row.window.start_utc.astimezone(OSLO).hour].append(row.uncontrolled_kwh)
    return {hour: sum(v) / len(v) for hour, v in sums.items()}


def test_no_leak_with_lagging_meter() -> None:
    """A meter one hour late: the lag is found and "other usage" is ≈ 1 kWh every hour."""
    rows, heater = synth(meter_lag_h=1)
    history = uncontrolled_history(rows, [heater], window_min=60, tz=OSLO)
    assert history.lag_h == 1
    for hour, kwh in by_local_hour(history).items():
        assert 0.85 < kwh < 1.15, (hour, kwh)


def test_no_leak_without_lag() -> None:
    """A meter in step: no lag, no leak."""
    rows, heater = synth(meter_lag_h=0)
    history = uncontrolled_history(rows, [heater], window_min=60, tz=OSLO)
    assert history.lag_h == 0
    assert all(0.85 < kwh < 1.15 for kwh in by_local_hour(history).values())


def test_round3_behaviour_leaks() -> None:
    """Forcing lag 0 on a lagging meter reproduces the live bump (≈ +3,5 kWh after the heater hours)."""
    rows, heater = synth(meter_lag_h=1)
    history = uncontrolled_history(rows, [heater], window_min=60, tz=OSLO, meter_lag_h=0)
    assert by_local_hour(history)[0] > 3.0  # the 23:00 run shows up in the 00:00 hour


def test_short_history_load_is_not_subtracted_from_older_hours() -> None:
    """Two days of heater history: the older hours are skipped, not counted whole."""
    rows, heater = synth(meter_lag_h=0)
    # Only 2 days of data for the heater (like PowerPlan's own energy sensor): older hours are skipped.
    cutoff = heater.power_rows[-1][0] - timedelta(days=2)
    short = ControlledHistory(
        load_id="vvb",
        nameplate_w=3500.0,
        power_rows=tuple(r for r in heater.power_rows if r[0] > cutoff),
    )
    history = uncontrolled_history(rows, [short], window_min=60, tz=OSLO, meter_lag_h=0)
    assert history.skipped > 24 * 20
    assert all(0.85 < kwh < 1.15 for kwh in by_local_hour(history).values())
