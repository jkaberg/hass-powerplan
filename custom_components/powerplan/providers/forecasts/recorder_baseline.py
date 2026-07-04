"""Recorder history → D10's baseline seed (D10 §5.2, §3).

`async_seed` is the whole job: read the site's own cumulative import register
and each controlled load's own power (or on/off) history through the
recorder's statistics tables - 5-minute short-term statistics for the last
`RECENT_DAYS` (HA's own short-term retention), hourly long-term statistics
beyond, per D10 §5.2 - turn that into `core/forecasts/reconstruct.py`'s
`ControlledHistory` rows, and fold the result into a live `HourOfWeekBaseline`
through the exact same `update()` the live planning loop uses per closed
window (D10 §5.1), so seeding and living update are one code path, not two.

`RecorderBaselineSource` is the thin registry entry D10 §6's config flow
needs (the source's own `half_life_days`/`t_ref_c` options, and the reason it
is "always on when a site meter exists" rather than asked about) - its own
`fetch()` answers the *forward* question every other source answers (the
baseline's own prediction out to `horizon`), while `async_seed` is the
*backward* one, called directly at setup and on `rebuild_baseline` (D10 §5.2),
not through the generic per-source dispatch every other kind uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Final

from homeassistant.components.recorder import history as recorder_history
from homeassistant.components.recorder import statistics as recorder_statistics
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

from custom_components.powerplan.core.forecasts.model import ForecastKind, Series, SeriesPoint
from custom_components.powerplan.core.forecasts.reconstruct import (
    ControlledHistory,
    uncontrolled_history,
)
from custom_components.powerplan.core.forecasts.registry import register
from custom_components.powerplan.core.pricing.model import Field, FieldKind

from .base import ForecastUnavailableError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import tzinfo

    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.core.forecasts.baseline import HourOfWeekBaseline
    from custom_components.powerplan.core.pricing.model import Schema

_LOGGER = logging.getLogger(__name__)

#: HA's own short-term statistics retention - beyond this, only the hourly
#: long-term table has anything left (D10 §5.2).
RECENT_DAYS: Final = 10
SHORT_TERM_PERIOD: Final = "5minute"
LONG_TERM_PERIOD: Final = "hour"

#: D10 §5.2: seeding reaches back 60 days by default - the same span D10 §5.6
#: gives the fits, so one recorder pass can serve both once fits are wired.
SEED_SPAN_DAYS: Final = 60
#: The window length a seed bins at - see `async_seed`'s own comment.
LONG_TERM_WINDOW_MIN: Final = 60


@dataclass(frozen=True, slots=True)
class LoadSource:
    """One load's own recorder entities: a power sensor, or an on/off one (D10 §2)."""

    load_id: str
    nameplate_w: float
    power_entity_id: str | None = None
    on_off_entity_id: str | None = None


async def async_site_register_kwh(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, float]]:
    """Return the site's cumulative import register, recent and long-term merged (D10 §5.2).

    A `total_increasing` register's own statistic is `sum` - HA's running total
    for the period, which is exactly the cumulative trace
    `reconstruct.uncontrolled_history` integrates between (D3 §5.11's own
    convention, `rows: Iterable[tuple[datetime, float]]`).
    """
    return await _statistic_rows(hass, entity_id, start, end, stat_type="sum")


async def async_load_power_w(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> tuple[tuple[datetime, float], ...]:
    """Return a power sensor's own mean-per-period trace (D10 §2's `full` row)."""
    rows = await _statistic_rows(hass, entity_id, start, end, stat_type="mean")
    return tuple(rows)


async def async_load_on_off(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> tuple[tuple[datetime, bool], ...]:
    """Return a switch-shaped entity's own state history (D10 §2's `partial` row).

    States have no long-term statistic, so this reads the recorder's raw
    history directly rather than the statistics tables - the only place in
    this module that does, and the reason it is its own function.
    """
    instance = get_instance(hass)
    raw = await instance.async_add_executor_job(
        recorder_history.get_significant_states,
        hass,
        start,
        end,
        [entity_id],
    )
    states = raw.get(entity_id, [])
    out: list[tuple[datetime, bool]] = []
    for state in states:
        value = state.state if hasattr(state, "state") else state.get("state")
        at = state.last_changed if hasattr(state, "last_changed") else None
        if at is None or value in (None, "unknown", "unavailable"):
            continue
        out.append((at, str(value).lower() in ("on", "true", "1", "heating", "cooling")))
    return tuple(out)


async def _statistic_rows(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime, *, stat_type: str
) -> list[tuple[datetime, float]]:
    """Merge 5-minute short-term and hourly long-term statistics into one trace."""
    instance = get_instance(hass)
    recent_from = max(start, end - timedelta(days=RECENT_DAYS))
    rows: list[tuple[datetime, float]] = []
    if recent_from < end:
        query = _StatQuery(recent_from, end, entity_id, SHORT_TERM_PERIOD, stat_type)
        rows.extend(await instance.async_add_executor_job(_query, hass, query))
    if start < recent_from:
        query = _StatQuery(start, recent_from, entity_id, LONG_TERM_PERIOD, stat_type)
        rows.extend(await instance.async_add_executor_job(_query, hass, query))
    rows.sort(key=lambda row: row[0])
    return rows


@dataclass(frozen=True, slots=True)
class _StatQuery:
    """One `statistics_during_period` call's own arguments, bundled for the executor job."""

    start: datetime
    end: datetime
    entity_id: str
    period: str
    stat_type: str


def _query(hass: HomeAssistant, query: _StatQuery) -> list[tuple[datetime, float]]:
    """Run one `statistics_during_period` call - always inside the recorder's executor."""
    answer = recorder_statistics.statistics_during_period(
        hass,
        query.start,
        query.end,
        {query.entity_id},
        query.period,  # type: ignore[arg-type]
        None,
        {query.stat_type},  # type: ignore[arg-type]
    )
    out: list[tuple[datetime, float]] = []
    for row in answer.get(query.entity_id, ()):
        value = row.get(query.stat_type)
        start_ts = row.get("start")
        if not isinstance(value, int | float) or not isinstance(start_ts, int | float):
            continue
        out.append((dt_util.utc_from_timestamp(start_ts), float(value)))
    return out


async def async_seed(
    hass: HomeAssistant,
    baseline: HourOfWeekBaseline,
    *,
    register_entity_id: str,
    loads: Sequence[LoadSource],
    now: datetime,
    tz: tzinfo,
    span_days: int = SEED_SPAN_DAYS,
) -> None:
    """Read the recorder and fold every historical window into `baseline` (D10 §5.2).

    Runs once at setup and again on the `rebuild_baseline` service - both
    times through this same function, an executor-bound read followed by the
    same pure `update()` the live planning loop calls per closed window, so a
    seeded baseline and a lived-into one are built the identical way.
    """
    start = now - timedelta(days=span_days)
    site_rows = await async_site_register_kwh(hass, register_entity_id, start, now)
    if not site_rows:
        raise ForecastUnavailableError(
            f"{register_entity_id}: no recorder history in the last {span_days} days"
        )

    controlled: list[ControlledHistory] = []
    for load in loads:
        power_rows: tuple[tuple[datetime, float], ...] = ()
        on_rows: tuple[tuple[datetime, bool], ...] = ()
        if load.power_entity_id is not None:
            power_rows = await async_load_power_w(hass, load.power_entity_id, start, now)
        elif load.on_off_entity_id is not None:
            on_rows = await async_load_on_off(hass, load.on_off_entity_id, start, now)
        controlled.append(
            ControlledHistory(
                load_id=load.load_id,
                nameplate_w=load.nameplate_w,
                power_rows=power_rows,
                on_rows=on_rows,
            )
        )

    # Seeded at hourly granularity regardless of the site's own window_min:
    # `HourOfWeekBaseline.update` bins by the hour-of-week either way (D10
    # §5.1's "4 updates/h" is for the *live* quarter-hour case), and hourly
    # long-term statistics beyond `RECENT_DAYS` could never yield a finer one.
    history = uncontrolled_history(site_rows, controlled, window_min=LONG_TERM_WINDOW_MIN, tz=tz)
    for window in history.windows:
        baseline.update(window.window, window.uncontrolled_kwh)
    _LOGGER.debug(
        "recorder_baseline: seeded %d window(s), reconstruction=%s",
        len(history.windows),
        history.reconstruction.value,
    )


@register
class RecorderBaselineSource:
    """v1's baseline source: the registry entry D10 §6's config flow renders (D10 §3, §6).

    Built directly by `runtime.py` around the site's own live baseline, the
    same reason `WeatherEntitySource` is - `@register` is for discoverability
    and D10 §6's schema rendering, not `registry.build()` construction.
    """

    key: ClassVar[str] = "recorder_baseline"
    kind: ClassVar[ForecastKind] = ForecastKind.BASELINE
    schema: ClassVar[Schema] = (
        Field(key="half_life_days", kind=FieldKind.NUMBER, default=28.0, advanced=True),
        Field(key="t_ref_c", kind=FieldKind.NUMBER, default=15.0, advanced=True),
    )

    def __init__(self, hass: HomeAssistant, *, baseline: HourOfWeekBaseline) -> None:
        """Bind the source to the site's own live baseline (already seeded and updated)."""
        del hass  # forward prediction reads only the baseline already in memory
        self._baseline = baseline

    async def fetch(self, horizon: timedelta, now: datetime) -> Series:
        """Return the baseline's own forward prediction out to `horizon` (not a seed - `async_seed` is)."""
        points: list[SeriesPoint] = []
        cursor = now
        end = now + horizon
        while cursor < end:
            nxt = min(cursor + timedelta(hours=1), end)
            mean_w, _sigma, confidence = self._baseline.predict(cursor)
            points.append(SeriesPoint(start=cursor, end=nxt, value=mean_w, confidence=confidence))
            cursor = nxt
        return Series(
            kind=ForecastKind.BASELINE,
            unit="W",
            points=tuple(points),
            source=self.key,
            issued_at=now,
        )


__all__ = [
    "LoadSource",
    "RecorderBaselineSource",
    "async_load_on_off",
    "async_load_power_w",
    "async_seed",
    "async_site_register_kwh",
]
