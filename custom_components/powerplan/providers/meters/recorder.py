"""The import register's history, read back from the recorder (D2 §2, D3 §5.11).

D2 §2 seeds a period "from the recorder first": the cumulative import register,
turned into closed windows by D3's `reconstruct_windows`. The rows are Home
Assistant's hourly long-term statistics - compiled for every closed hour and
never purged - read in the recorder's own executor, never on the event loop.
Hourly rows make exact 60-minute windows; a quarter-hour tariff gets them split
and marked `coarse` by D2 §5.1, as §2 prescribes for hourly history.

A statistics row is filed under its hour's start S, but its `sum` is the
register at the last report *inside* the hour (`sensor/recorder.py` keeps the
last state of the period). When that report is depends on the meter:

| the register reports | the last report in [S, S + 1 h) carries | placed at |
|---|---|---|
| once an hour, at the boundary (an AMS meter; D3 §5.5's latched mode) | the register at S | S |
| every c seconds, c < 1 h | the register at S + 1 h − c | S + 1 h − c |

So a row is placed at `S + 1 h − min(c, 1 h)`, with `c` the median gap between the register's
own recent *changes of value*. The reference house reports at HH:00:10 (its recorder backtest:
"anchor `start`"); a register read every few seconds lands a whole hour late without this. A
value republished after `unavailable` - every restart does it, several times a day in the house -
is not a report, or the restarts would pull the median under the hour (D3 §5.5's
`near_boundary` lesson). With no changes to measure, D3 §5.3's default holds: latched until a
cadence is known.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import TYPE_CHECKING, Final

from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.recorder import statistics as recorder_statistics
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

__all__ = ["async_register_history", "recorder_loaded"]

RECORDER_DOMAIN: Final = "recorder"
HOUR: Final = timedelta(hours=1)
NO_SHIFT: Final = timedelta(0)
#: How far back, and how many of the register's own state rows, the cadence is taken from.
CADENCE_LOOKBACK: Final = timedelta(days=1)
CADENCE_ROWS: Final = 50
#: Gaps needed for a median - D3 §5.3's `CADENCE_MIN_SAMPLES`.
CADENCE_MIN_GAPS: Final = 5
#: Statistics are asked for in kWh, whatever unit the register declares.
UNITS: Final = {"energy": "kWh"}


def recorder_loaded(hass: HomeAssistant) -> bool:
    """Whether the recorder is set up: without it there is no history to read."""
    return RECORDER_DOMAIN in hass.config.components


async def async_register_history(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Return `(instant, kWh)` register rows bracketing `[start, end]`, oldest first.

    Each row is placed at the instant its value refers to (the module's table),
    so `reconstruct_windows` interpolates at boundaries it can trust. One row
    before `start` is included, which is what closes the window starting there.
    """
    instance = get_instance(hass)
    cadence_s = await instance.async_add_executor_job(_report_cadence_s, hass, entity_id, end)
    rows = await instance.async_add_executor_job(_hourly_sums, hass, entity_id, start - HOUR, end)
    shift = HOUR - min(timedelta(seconds=cadence_s), HOUR) if cadence_s is not None else NO_SHIFT
    return [(at + shift, kwh) for at, kwh in rows]


def _hourly_sums(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Read the register's hourly `sum` rows - always inside the recorder's executor."""
    answer = recorder_statistics.statistics_during_period(
        hass, start, end, {entity_id}, "hour", UNITS, {"sum"}
    )
    out: list[tuple[datetime, float]] = []
    for row in answer.get(entity_id, ()):
        value = row.get("sum")
        at = row.get("start")
        if isinstance(value, int | float) and isinstance(at, int | float):
            out.append((dt_util.utc_from_timestamp(at), float(value)))
    out.sort(key=lambda row: row[0])
    return out


def _report_cadence_s(hass: HomeAssistant, entity_id: str, end: datetime) -> float | None:
    """Return the median gap between the register's recent state rows, or `None`."""
    states = recorder_history.state_changes_during_period(
        hass,
        end - CADENCE_LOOKBACK,
        end,
        entity_id,
        no_attributes=True,
        descending=True,
        limit=CADENCE_ROWS,
        include_start_time_state=False,
    ).get(entity_id, [])
    readings = sorted(
        (state.last_updated.timestamp(), value)
        for state in states
        if (value := _kwh(state.state)) is not None
    )
    changes: list[float] = []
    previous: float | None = None
    for at, value in readings:
        if value != previous:
            changes.append(at)
        previous = value
    gaps = [b - a for a, b in pairwise(changes) if b > a]
    return median(gaps) if len(gaps) >= CADENCE_MIN_GAPS else None


def _kwh(raw: str) -> float | None:
    """Return a state row's number, or `None` for `unavailable`, `unknown` and the like."""
    try:
        return float(raw)
    except ValueError:
        return None
