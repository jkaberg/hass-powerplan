"""Fixtures for the backtest tool's tests (D9 §5.4).

Two writers, one history. `write_recorder` builds a synthetic Home Assistant
recorder database - the statistics subset `tools/backtest.py` reads, with the
column list taken verbatim from `homeassistant.components.recorder.db_schema` at
HA 2026.9.3 - and `write_csv` writes the same rows in the CSV layout the tool
documents. A test that asserts the two agree therefore compares two readers, not
two fixtures.

The history itself comes from `tests/builders/histories.py`: a `Trace` is
piecewise linear, so `trace.energy_kwh(a, b)` is the *analytic* energy of a
window and `trace.register_at(t)` the register that must produce it. Expected
window energies are computed from the trace, never written down twice.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

import pytest

from tests.builders.histories import Trace, steps

if TYPE_CHECKING:
    from pathlib import Path

OSLO = ZoneInfo("Europe/Oslo")

#: The tables `tools/backtest.py` opens, with HA 2026.9.3's columns. `CHAR(0)`
#: is what HA's own DDL emits for the legacy datetime columns it no longer
#: writes; they are here so the reader meets the shape it will meet in the
#: house, including the ones it must ignore.
SCHEMA_SQL = """
CREATE TABLE statistics_meta (
    id INTEGER NOT NULL,
    statistic_id VARCHAR(255),
    source VARCHAR(32),
    unit_of_measurement VARCHAR(255),
    unit_class VARCHAR(255),
    has_mean BOOLEAN,
    has_sum BOOLEAN,
    name VARCHAR(255),
    mean_type SMALLINT NOT NULL,
    PRIMARY KEY (id)
);
CREATE TABLE statistics (
    id INTEGER NOT NULL,
    created CHAR(0),
    created_ts FLOAT,
    metadata_id INTEGER,
    start CHAR(0),
    start_ts FLOAT,
    mean FLOAT,
    mean_weight FLOAT,
    min FLOAT,
    max FLOAT,
    last_reset CHAR(0),
    last_reset_ts FLOAT,
    state FLOAT,
    sum FLOAT,
    PRIMARY KEY (id)
);
CREATE TABLE statistics_short_term (
    id INTEGER NOT NULL,
    created CHAR(0),
    created_ts FLOAT,
    metadata_id INTEGER,
    start CHAR(0),
    start_ts FLOAT,
    mean FLOAT,
    mean_weight FLOAT,
    min FLOAT,
    max FLOAT,
    last_reset CHAR(0),
    last_reset_ts FLOAT,
    state FLOAT,
    sum FLOAT,
    PRIMARY KEY (id)
);
CREATE TABLE states_meta (
    metadata_id INTEGER NOT NULL,
    entity_id VARCHAR(255),
    PRIMARY KEY (metadata_id)
);
CREATE TABLE states (
    state_id INTEGER NOT NULL,
    entity_id CHAR(0),
    state VARCHAR(255),
    last_changed_ts FLOAT,
    last_reported_ts FLOAT,
    last_updated_ts FLOAT,
    metadata_id INTEGER,
    PRIMARY KEY (state_id)
);
"""


@dataclass
class Stat:
    """One statistic series as the recorder holds it."""

    statistic_id: str
    unit: str
    #: `(period start, value)`. A `sum` series' value is the register at the END
    #: of the period, which is how HA compiles a `total_increasing` sensor; a
    #: `mean` series' value is the period's mean.
    rows: Sequence[tuple[datetime, float]]
    kind: Literal["sum", "mean"] = "sum"
    short_term: bool = False


@dataclass
class States:
    """One on/off entity as the recorder holds its raw states."""

    entity_id: str
    rows: Sequence[tuple[datetime, str]]


def write_recorder(
    path: Path,
    stats: Sequence[Stat] = (),
    states: Sequence[States] = (),
) -> Path:
    """Write a recorder database holding `stats` and `states`; return its path."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_SQL)
        for index, stat in enumerate(stats, start=1):
            connection.execute(
                "INSERT INTO statistics_meta"
                " (id, statistic_id, source, unit_of_measurement, unit_class,"
                "  has_mean, has_sum, name, mean_type)"
                " VALUES (?, ?, 'recorder', ?, NULL, ?, ?, NULL, ?)",
                (
                    index,
                    stat.statistic_id,
                    stat.unit,
                    stat.kind == "mean",
                    stat.kind == "sum",
                    1 if stat.kind == "mean" else 0,
                ),
            )
            table = "statistics_short_term" if stat.short_term else "statistics"
            column = "sum" if stat.kind == "sum" else "mean"
            connection.executemany(
                f"INSERT INTO {table} (metadata_id, start_ts, {column}, state) VALUES (?, ?, ?, ?)",
                [(index, at.timestamp(), value, value) for at, value in stat.rows],
            )
        for index, entity in enumerate(states, start=1):
            connection.execute(
                "INSERT INTO states_meta (metadata_id, entity_id) VALUES (?, ?)",
                (index, entity.entity_id),
            )
            connection.executemany(
                "INSERT INTO states (metadata_id, state, last_updated_ts) VALUES (?, ?, ?)",
                [(index, state, at.timestamp()) for at, state in entity.rows],
            )
        connection.commit()
    finally:
        connection.close()
    return path


def write_csv(
    directory: Path,
    *,
    register: Sequence[tuple[datetime, float]] = (),
    power: Sequence[tuple[datetime, float]] = (),
    load_power: dict[str, Sequence[tuple[datetime, float]]] | None = None,
    load_energy: dict[str, Sequence[tuple[datetime, float]]] | None = None,
    load_onoff: dict[str, Sequence[tuple[datetime, int]]] | None = None,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Write the CSV layout `tools/backtest.py` documents; return the directory."""
    directory.mkdir(parents=True, exist_ok=True)
    loads = directory / "loads"
    if register:
        _rows(directory / "grid_register.csv", "timestamp,kwh", register)
    if power:
        _rows(directory / "grid_power.csv", "timestamp,w", power)
    for load_id, rows in (load_power or {}).items():
        loads.mkdir(exist_ok=True)
        _rows(loads / f"{load_id}.power.csv", "timestamp,w", rows)
    for load_id, rows in (load_energy or {}).items():
        loads.mkdir(exist_ok=True)
        _rows(loads / f"{load_id}.energy.csv", "timestamp,kwh", rows)
    for load_id, rows in (load_onoff or {}).items():
        loads.mkdir(exist_ok=True)
        _rows(loads / f"{load_id}.onoff.csv", "timestamp,on", rows)
    if meta is not None:
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory


def _rows(path: Path, header: str, rows: Sequence[tuple[datetime, float | int]]) -> None:
    lines = [header, *(f"{at.isoformat()},{value!r}" for at, value in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# the history
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class House:
    """A synthetic house: one grid trace, and the rows every reader wants."""

    trace: Trace
    tz: tzinfo = OSLO
    start_kwh: float = 12_345.0
    loads: dict[str, Trace] = field(default_factory=dict)

    def hours(self) -> list[datetime]:
        """Every whole hour inside the trace, as UTC instants."""
        first = self.trace.start.replace(minute=0, second=0, microsecond=0)
        if first < self.trace.start:
            first += timedelta(hours=1)
        out: list[datetime] = []
        at = first
        while at <= self.trace.end:
            out.append(at)
            at += timedelta(hours=1)
        return out

    def register(self, trace: Trace | None = None) -> list[tuple[datetime, float]]:
        """Hourly LTS `sum` rows: `(hour start, register at the hour's END)`.

        The first row is the hour *before* the trace, carrying the register at the
        trace's own start - the house existed before the window under test, and
        without that row the trace's first hour has no opening value.
        """
        use = trace if trace is not None else self.trace
        start = self.start_kwh if trace is None else 0.0
        rows = [(use.start - timedelta(hours=1), use.register_at(use.start, start))]
        rows.extend(
            (at, use.register_at(at + timedelta(hours=1), start))
            for at in self.hours()[:-1]
            if at + timedelta(hours=1) <= use.end
        )
        return rows

    def means(
        self, cadence_min: int = 5, trace: Trace | None = None
    ) -> list[tuple[datetime, float]]:
        """`mean` rows every `cadence_min`, exact for a piecewise-linear trace."""
        use = trace if trace is not None else self.trace
        out: list[tuple[datetime, float]] = []
        at = use.start
        step = timedelta(minutes=cadence_min)
        while at + step <= use.end:
            out.append((at, use.energy_kwh(at, at + step) * 3.6e6 / step.total_seconds()))
            at += step
        return out

    def window_kwh(self, window_min: int = 60) -> dict[datetime, float]:
        """Return the analytic energy of every whole window inside the trace."""
        out: dict[datetime, float] = {}
        for at in self.hours()[:-1]:
            for index in range(60 // window_min):
                start = at + timedelta(minutes=window_min * index)
                end = start + timedelta(minutes=window_min)
                if end <= self.trace.end:
                    out[start] = self.trace.energy_kwh(start, end)
        return out


def flat_hours(start: datetime, kw: Sequence[float]) -> Trace:
    """Build a trace holding each hour's kW, one point per hour boundary.

    The trace is piecewise linear, so an hour between two equal points has
    exactly that energy and an hour between two different ones has their mean -
    which is why every expectation is read back off the trace.
    """
    return steps(start, 3600.0, [value * 1000.0 for value in kw])


def september(kw: Sequence[float], *, day: int = 1) -> Trace:
    """`flat_hours` anchored at a September midnight in Oslo (UTC-keyed)."""
    return flat_hours(
        datetime(2026, 9, day, 0, 0, tzinfo=OSLO).astimezone(UTC),
        kw,
    )


@pytest.fixture
def oslo() -> tzinfo:
    """Return the reference house's zone."""
    return OSLO
