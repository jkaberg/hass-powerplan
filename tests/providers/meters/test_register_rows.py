"""D3 §9 22 - one reader places every register row, both statistics tables (D-0687).

HA files a statistics row under its period's start, and its `sum` is the register
after the last report inside the period. D10's seed read the 5-minute rows at their
start: on a register reporting every 10 s that ran the meter a period early, and the
reference house's baseline held 2 990 W at 21:00 against 1 376 W measured. Every
reader now places a row at `S + period − min(c, period)`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.powerplan.providers.meters import recorder as reader

END = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


class _Instance:
    async def async_add_executor_job(self, func: Any, *args: Any) -> Any:
        return func(*args)


def _fake(rows: dict[str, list[tuple[datetime, float]]]) -> Any:
    def fake(hass, start, end, statistic_ids, period, units, types):  # noqa: PLR0917
        """Serve `rows` for the table `period` names."""
        del hass, statistic_ids, units, types
        return {
            "sensor.register": [
                {"start": at.timestamp(), "sum": value}
                for at, value in rows[period]
                if start <= at < end
            ]
        }

    return fake


async def _rows(cadence_s: float | None) -> list[tuple[datetime, float]]:
    hourly_start = END - timedelta(days=11)
    recent_from = END - timedelta(days=reader.RECENT_DAYS)
    rows = {
        "hour": [(hourly_start + timedelta(hours=h), 100.0 + h) for h in range(24)],
        "5minute": [(recent_from + timedelta(minutes=5 * i), 500.0 + i) for i in range(3)],
    }
    with (
        patch.object(reader, "get_instance", return_value=_Instance()),
        patch.object(reader, "_report_cadence_s", return_value=cadence_s),
        patch.object(
            reader.recorder_statistics, "statistics_during_period", side_effect=_fake(rows)
        ),
    ):
        return await reader.async_register_rows(None, "sensor.register", hourly_start, END)  # type: ignore[arg-type]


async def test_22_a_ten_second_register_is_placed_at_each_period_s_end() -> None:
    """Each row lands 10 s before its period's end, in both tables, oldest first."""
    rows = await _rows(10.0)
    hourly = [row for row in rows if row[1] < 500.0]
    recent = [row for row in rows if row[1] >= 500.0]
    start = END - timedelta(days=11)
    recent_from = END - timedelta(days=reader.RECENT_DAYS)

    assert hourly[0] == (start + timedelta(minutes=59, seconds=50), pytest.approx(100.0))
    assert recent[0] == (recent_from + timedelta(minutes=4, seconds=50), pytest.approx(500.0))
    assert rows == sorted(rows, key=lambda row: row[0])


async def test_22_a_latched_register_stays_at_each_period_s_start() -> None:
    """A register that reports once an hour: its value is the one at S (D3 §5.5)."""
    rows = await _rows(3600.0)
    start = END - timedelta(days=11)
    recent_from = END - timedelta(days=reader.RECENT_DAYS)

    assert rows[0][0] == start
    assert next(at for at, value in rows if value >= 500.0) == recent_from


def test_22_only_the_one_reader_asks_the_recorder_for_a_sum() -> None:
    """A second reader of the register is how the baseline ended up an hour out of step."""
    package = Path(reader.__file__).resolve().parents[2]
    offenders = [
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if path != Path(reader.__file__).resolve() and '"sum"' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
