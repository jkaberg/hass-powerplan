"""Shared fixtures for the D10 forecast tests (D9 §3).

Nothing here starts Home Assistant: `tests/core/` is the pure half of the suite
(D9 §3). The site zone is a value the tests pass in - `core/` never
names one (`design/DECISIONS.md` D-0100) - and the physics the fits have to
recover from comes from `tests/sim/`, never from a static mock.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from custom_components.powerplan.core.forecasts import (
    BaselineState,
    Bin,
    HourOfWeekBaseline,
    Reconstruction,
)
from custom_components.powerplan.core.metering import AnchorKind, ClosedWindow, window_bounds

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import tzinfo

OSLO = ZoneInfo("Europe/Oslo")

#: A Tuesday in the middle of January, local time - an ordinary winter weekday.
NOW = datetime(2026, 1, 13, 18, 0, tzinfo=OSLO)

#: The EU DST days of the benchmark year, both Sundays (D9 §5.3).
SPRING_FORWARD = datetime(2026, 3, 29, 0, 0, tzinfo=OSLO)
FALL_BACK = datetime(2026, 10, 25, 0, 0, tzinfo=OSLO)


def local(*args: int, tz: tzinfo = OSLO) -> datetime:
    """Build a local wall-clock instant in the site's zone."""
    return datetime(*args, tzinfo=tz)


def closed(start: datetime, kwh: float, minutes: int = 15) -> ClosedWindow:
    """Build one `ClosedWindow` of `minutes` carrying `kwh` (D3 §4)."""
    return ClosedWindow(
        start_utc=start.astimezone(UTC),
        window_min=minutes,
        kwh=kwh,
        avg_kw=kwh / (minutes / 60.0),
        anchor_kind=AnchorKind.REGISTER_LATCHED,
        degraded=False,
        confidence="exact",
    )


def day_windows(day: datetime, minutes: int, tz: tzinfo = OSLO) -> tuple[ClosedWindow, ...]:
    """Every window of the local day `day` starts on, at `minutes` each.

    Walked through `window_bounds`, so a DST day yields 92 or 100 quarter
    windows and the repeated autumn hour yields two with distinct UTC starts
    (D3 §5.2).
    """
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)
    end = (datetime(day.year, day.month, day.day, tzinfo=tz) + timedelta(days=1)).astimezone(UTC)
    out: list[ClosedWindow] = []
    cursor = start
    while cursor < end:
        bounds = window_bounds(cursor, minutes, tz)
        out.append(closed(bounds[0], kwh=0.5, minutes=minutes))
        cursor = bounds[1]
    return tuple(out)


def seeded(
    weight: float,
    mean_w: float,
    *,
    sigma_w: float = 0.0,
    at: datetime = NOW,
    hours: Sequence[int] | None = None,
    weekday: int | None = None,
    half_life_days: float = 28.0,
) -> HourOfWeekBaseline:
    """Return a baseline whose bins already hold `weight` samples of `mean_w`.

    Item 3 is about the *gate*, not about the accumulation item 1 pins, so the
    bins are built at the weight the test is about instead of driven there
    through `update()` - which is also how a baseline arrives from the store.
    """
    filled = Bin(
        mean_w=mean_w,
        m2=sigma_w * sigma_w * weight,
        weight=weight,
        samples=round(weight),
    )
    bins: list[Bin] = []
    for index in range(168):
        day, hour = divmod(index, 24)
        wanted = (weekday is None or day == weekday) and (hours is None or hour in hours)
        bins.append(filled if wanted else Bin())
    state = BaselineState(
        bins=tuple(bins),
        half_life_days=half_life_days,
        last_update=at.astimezone(UTC),
        reconstruction=Reconstruction.FULL,
    )
    return HourOfWeekBaseline(state, tz=OSLO)


# --------------------------------------------------------------------------- #
# BaselineState round-trip (D10 §7)
# --------------------------------------------------------------------------- #
# D7 owns the store; this is the shape it has to be able to write. Everything in
# `BaselineState` is a primitive, an ISO-8601 string, a `StrEnum` or a tuple of
# those, and the decoder is deliberately explicit so a divergence shows up as a
# test failure rather than as a silent `str()`.


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Bin):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def roundtrip(state: BaselineState) -> BaselineState:
    """`state` through JSON and back, as D7's store will take it (D10 §7)."""
    encoded = {f.name: _encode(getattr(state, f.name)) for f in fields(state)}
    revived: dict[str, Any] = json.loads(json.dumps(encoded))
    last = revived["last_update"]
    return BaselineState(
        bins=tuple(Bin(**row) for row in revived["bins"]),
        t_ref_c=revived["t_ref_c"],
        half_life_days=revived["half_life_days"],
        last_update=None if last is None else datetime.fromisoformat(last),
        reconstruction=Reconstruction(revived["reconstruction"]),
        schema=revived["schema"],
    )
