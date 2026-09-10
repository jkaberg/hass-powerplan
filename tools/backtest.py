#!/usr/bin/env python3
"""Replay real history through D3's window reconstruction and D2's bill (D9 §5.4).

`uv run python tools/backtest.py --recorder <copy.db> --register <entity> --tariff
<tariff.json>` reads a Home Assistant recorder database (a **copy**, opened
read-only), rebuilds the windows the tariff measures, bills them month by month
and prints what the history would have cost. `--csv <dir>` does the same from an
export, for a house whose recorder is gone or was never Norwegian.

The tool judges nothing. It reports the numbers and how good the reconstruction
behind them was, because on a real recorder the answer is usually "partly": raw
`states` are purged after days, long-term statistics are hourly, and per-load
power may never have been recorded at all (PLAN §6 R8). Three honesty rules
follow from that:

* a window whose register rows are missing is **not** interpolated across the
  gap - it is taken from the power history if that covers it (marked
  `estimated`), and otherwise dropped and counted in a note;
* hourly statistics against a quarter-hour tariff are handed to D2 as hourly
  windows, which splits them and marks every part `coarse` (D3 §5.11, D2 §5.1);
* a load with no power or energy history is `nameplate × on_fraction` from its
  on/off states, and the month reads `reconstruction = partial`; a load with no
  history at all reads `missing`, never zero.

`--simulate` (replaying through the engine with the historical controlled loads
replaced by simulators) is the other half of D9 §5.4 and lands with WP0.9's
runner; until then `cost_energy`, `cost_counterfactual` and `savings` are `None`
and a note says so (D11 arrives in WP0.10).

**The CSV layout.** One directory, UTF-8, a header line per file, timestamps
ISO-8601 with an offset (or trailing `Z`):

```
grid_register.csv          timestamp,kwh    cumulative import register
grid_power.csv             timestamp,w      signed grid power (optional)
loads/<load_id>.energy.csv timestamp,kwh    cumulative per-load register
loads/<load_id>.power.csv  timestamp,w      per-load power
loads/<load_id>.onoff.csv  timestamp,on     1/0, on/off, true/false
meta.json                  {"tz": "Europe/Oslo", "window_min": 60}   (optional)
```

**The zone is never guessed.** `--tz` wins, then `meta.json`, then a
`.storage/core.config` next to the recorder database, then the `tz` the tariff
file carries (D-0111).

**The tariff** is a JSON file (D13 §12.2): a stored copy - an entry's
`tariff.price`, or the whole `tariff` section - or a rule-format file such as
the test fixtures' `tests/fixtures/presets/no/tensio-ts.json`. The integration
ships no company's prices (INV-70), so there is no name to look one up by. With none of those the tool refuses rather than pick a
zone, and the report always says which source it used.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # `python tools/backtest.py` from anywhere
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.powerplan.core.metering import (  # noqa: E402
    AnchorKind,
    ClosedWindow,
    reconstruct_windows,
    window_bounds,
)
from custom_components.powerplan.core.model import Money  # noqa: E402
from custom_components.powerplan.core.pricing.holidays import (  # noqa: E402
    NoHolidays,
    UnknownCalendarError,
    calendar_for,
)
from custom_components.powerplan.core.tariffs import (  # noqa: E402
    AUTO,
    Evaluator,
    PeakTariff,
    Target,
    TariffSpec,
    resolve_target_kw,
    seed_from_windows,
)
from custom_components.powerplan.core.tariffs.household import (  # noqa: E402
    from_json as price_from_json,
)
from custom_components.powerplan.core.tariffs.household import (  # noqa: E402
    spec as price_spec,
)
from custom_components.powerplan.core.tariffs.rules.loader import (  # noqa: E402
    PresetError,
)
from custom_components.powerplan.core.tariffs.rules.loader import (  # noqa: E402
    from_raw as spec_from_raw,
)

if TYPE_CHECKING:
    from custom_components.powerplan.core.tariffs import HolidayCalendar

_LOGGER = logging.getLogger("powerplan.backtest")


#: Units the tool reads as an energy register, and their factor to kWh.
ENERGY_UNITS = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
#: Units the tool reads as power, and their factor to W.
POWER_UNITS = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}
#: A boundary counts as measured when a register row sits this close to it.
BOUNDARY_TOLERANCE_S = 60.0
#: …or within half the register's own cadence, whichever is larger.
BOUNDARY_TOLERANCE_CADENCES = 0.5
#: A power fill has to cover this much of the window to be used at all.
POWER_COVER = 0.999
#: Default window length when neither the preset nor the flags name one.
FALLBACK_WINDOW_MIN = 60

#: Fewer rows than this cannot be scored against anything.
ANCHOR_MIN_ROWS = 3
#: How many register rows the anchor detection scores; a few days is plenty and
#: the whole span would cost a power lookup per row.
ANCHOR_SAMPLE = 200

Anchor = Literal["start", "end"]
Origin = Literal["register", "power"]
LoadQuality = Literal["energy", "power", "on_fraction", "missing"]
Reconstruction = Literal["full", "partial", "none"]

ON_STATES = frozenset({"on", "true", "1", "heat", "heating", "cooling", "charging"})


# --------------------------------------------------------------------------- #
# what comes out
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LoadSpec:
    """One load in the `--loads` mapping: an entity, a type and a nameplate."""

    load_id: str
    kind: str
    entity: str
    nameplate_w: float | None = None


@dataclass(frozen=True)
class Window:
    """One reconstructed window and where its energy came from."""

    closed: ClosedWindow
    origin: Origin


@dataclass(frozen=True)
class LoadHistory:
    """What a recorder or an export actually held for one load."""

    spec: LoadSpec
    quality: LoadQuality
    #: `(start, end, kwh)` - one entry per source row, so a period can be summed
    #: without re-reading the database.
    buckets: tuple[tuple[datetime, datetime, float], ...] = ()


@dataclass(frozen=True)
class SiteHistory:
    """The site's raw history, in the two shapes the reconstruction needs."""

    #: `(instant, cumulative kWh)` - the register AT that instant, not over it.
    register: tuple[tuple[datetime, float], ...] = ()
    #: `(start, span seconds, mean W)`.
    power: tuple[tuple[datetime, float, float], ...] = ()
    loads: tuple[LoadHistory, ...] = ()
    source: str = ""
    tz_name: str | None = None
    window_min: int | None = None
    requested: tuple[datetime | None, datetime | None] = (None, None)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BacktestMetrics:
    """D9 §4's metric set, for the half of it a plain replay can fill.

    `windows`, `over_target`, `coarse_windows` and `estimated_windows` count the
    windows the **tariff** measures - an hourly register against a quarter-hour
    tariff is four coarse windows per hour, exactly as D2 records it (§5.1).
    """

    windows: int
    over_target: int
    max_window_kwh: float
    level_reached: str
    fee: Money
    metric_kw: float
    target_kw: float
    gate: bool
    confidence: str
    coarse_windows: int
    estimated_windows: int
    days: int
    reconstruction: Reconstruction
    load_kwh: Mapping[str, float] = field(default_factory=dict)
    load_quality: Mapping[str, LoadQuality] = field(default_factory=dict)
    #: WP0.10 (D11) fills these; a plain replay has no counterfactual.
    cost_energy: Money | None = None
    cost_counterfactual: Money | None = None
    savings: Money | None = None

    def as_dict(self) -> dict[str, Any]:
        """Render the row for the JSON output."""
        return {
            "windows": self.windows,
            "over_target": self.over_target,
            "gate": self.gate,
            "max_window_kwh": round(self.max_window_kwh, 4),
            "metric_kw": round(self.metric_kw, 4),
            "target_kw": None if math.isinf(self.target_kw) else round(self.target_kw, 4),
            "level_reached": self.level_reached,
            "fee": str(self.fee.amount),
            "currency": self.fee.currency,
            "confidence": self.confidence,
            "coarse_windows": self.coarse_windows,
            "estimated_windows": self.estimated_windows,
            "days": self.days,
            "reconstruction": self.reconstruction,
            "load_kwh": {key: round(value, 3) for key, value in self.load_kwh.items()},
            "load_quality": dict(self.load_quality),
            "cost_energy": None,
            "cost_counterfactual": None,
            "savings": None,
        }


@dataclass(frozen=True)
class BacktestResult:
    """One run: the windows, the per-period metrics and what to distrust."""

    preset: str
    tz_name: str
    tz_source: str
    window_min: int
    target: str
    months: Mapping[str, BacktestMetrics]
    total: BacktestMetrics
    windows: tuple[Window, ...]
    span: tuple[datetime, datetime] | None
    notes: tuple[str, ...]
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Render the whole run for `--out`."""
        return {
            "tool": "tools/backtest.py",
            "mode": self.source,
            "preset": self.preset,
            "tz": self.tz_name,
            "tz_source": self.tz_source,
            "window_min": self.window_min,
            "target": self.target,
            "span": None
            if self.span is None
            else {"from": self.span[0].isoformat(), "to": self.span[1].isoformat()},
            "months": {key: value.as_dict() for key, value in self.months.items()},
            "total": self.total.as_dict(),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# the recorder
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Series:
    """One statistics series as `statistics_meta` describes it.

    Only the unit decides how it is read. `has_mean` was NULL and `has_sum` 0 on
    every power sensor of the reference house's database (HA had moved the
    information to `mean_type`), so a flag that changes name between releases is
    not something a backtest can depend on - but °C is never an energy register
    and kWh is never a power reading.
    """

    metadata_id: int
    statistic_id: str
    unit: str


def _connect(path: Path) -> sqlite3.Connection:
    """Open a recorder copy read-only, so the tool cannot write to it."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _series(connection: sqlite3.Connection) -> dict[str, _Series]:
    """Return every statistics series in the database, by `statistic_id`."""
    rows = connection.execute(
        "SELECT id, statistic_id, unit_of_measurement FROM statistics_meta"
    ).fetchall()
    return {
        str(statistic_id): _Series(
            metadata_id=int(metadata_id),
            statistic_id=str(statistic_id),
            unit=str(unit or ""),
        )
        for metadata_id, statistic_id, unit in rows
    }


def _stat_rows(
    connection: sqlite3.Connection,
    series: _Series,
    column: str,
    *,
    short_term: bool,
    start: datetime | None,
    end: datetime | None,
) -> list[tuple[float, float]]:
    table = "statistics_short_term" if short_term else "statistics"
    sql = (
        f"SELECT start_ts, {column} FROM {table} "
        "WHERE metadata_id = ? AND start_ts IS NOT NULL AND "
        f"{column} IS NOT NULL"
    )
    params: list[Any] = [series.metadata_id]
    if start is not None:
        sql += " AND start_ts >= ?"
        params.append(start.timestamp())
    if end is not None:
        sql += " AND start_ts <= ?"
        params.append(end.timestamp())
    sql += " ORDER BY start_ts"
    return [(float(at), float(value)) for at, value in connection.execute(sql, params)]


def _cadence_s(instants: Sequence[float]) -> float:
    gaps = [b - a for a, b in pairwise(instants) if b > a]
    return median(gaps) if gaps else 3600.0


def _read_energy_series(
    connection: sqlite3.Connection,
    series: _Series,
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[tuple[datetime, float]]:
    """Return `(row start, cumulative kWh)` rows for a `sum` series.

    Unshifted: a row is filed under its period's start, but what its `sum` refers
    to depends on when inside the period the sensor last reported - the end for a
    sensor that updates continuously, the start for a latched AMS register whose
    one report per hour carries the value at the boundary (D3 §5.5). `_anchor`
    settles that against the power history; nothing here assumes it.

    `statistics` (hourly) is the durable table and is read first. The 5-minute
    `statistics_short_term` sums, which survive about ten days, are read only when
    the hourly table has nothing - mixing the two would put two cadences in one
    register series for the sake of the last week.
    """
    factor = ENERGY_UNITS.get(series.unit)
    if factor is None:
        _LOGGER.warning(
            "%s is in %r, which is not an energy unit", series.statistic_id, series.unit
        )
        return []
    rows = _stat_rows(connection, series, "sum", short_term=False, start=start, end=end)
    if not rows:
        rows = _stat_rows(connection, series, "sum", short_term=True, start=start, end=end)
    return [(datetime.fromtimestamp(at, UTC), value * factor) for at, value in rows]


def _read_power_series(
    connection: sqlite3.Connection,
    series: _Series,
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[tuple[datetime, float, float]]:
    """Return `(start, span seconds, mean W)` rows for a `mean` series.

    Both tables, merged: `statistics_short_term` keeps 5-minute means for about
    ten days and `statistics` keeps hourly means for years, so the fine rows cover
    the recent tail and the hourly ones everything before it. Preferring one table
    outright would either throw away the resolution that makes a 15-minute window
    recoverable or throw away eleven months.
    """
    factor = POWER_UNITS.get(series.unit)
    if factor is None:
        _LOGGER.warning("%s is in %r, which is not a power unit", series.statistic_id, series.unit)
        return []
    fine = _stat_rows(connection, series, "mean", short_term=True, start=start, end=end)
    coarse = _stat_rows(connection, series, "mean", short_term=False, start=start, end=end)
    out: list[tuple[datetime, float, float]] = []
    covered_from: float | None = None
    covered_to = 0.0
    if fine:
        cadence = _cadence_s([at for at, _ in fine])
        out += [(datetime.fromtimestamp(at, UTC), cadence, value * factor) for at, value in fine]
        covered_from, covered_to = fine[0][0], fine[-1][0] + cadence
    if coarse:
        cadence = _cadence_s([at for at, _ in coarse])
        out += [
            (datetime.fromtimestamp(at, UTC), cadence, value * factor)
            for at, value in coarse
            if covered_from is None or at + cadence <= covered_from or at >= covered_to
        ]
    out.sort(key=lambda row: row[0])
    return out


def _read_states(
    connection: sqlite3.Connection,
    entity_id: str,
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[tuple[datetime, str]]:
    """Return the raw `(instant, state)` rows for an entity, oldest first."""
    try:
        sql = (
            "SELECT COALESCE(s.last_updated_ts, s.last_changed_ts) AS at, s.state "
            "FROM states AS s JOIN states_meta AS m ON m.metadata_id = s.metadata_id "
            "WHERE m.entity_id = ? AND s.state IS NOT NULL"
        )
        params: list[Any] = [entity_id]
        if start is not None:
            sql += " AND at >= ?"
            params.append(start.timestamp())
        if end is not None:
            sql += " AND at <= ?"
            params.append(end.timestamp())
        rows = connection.execute(sql + " ORDER BY at", params).fetchall()
    except sqlite3.OperationalError as err:  # a schema older than states_meta
        _LOGGER.warning("cannot read states for %s: %s", entity_id, err)
        return []
    return [
        (datetime.fromtimestamp(float(at), UTC), str(state))
        for at, state in rows
        if at is not None and str(state) not in {"unknown", "unavailable"}
    ]


def _on_buckets(
    rows: Sequence[tuple[datetime, str]], nameplate_w: float
) -> tuple[tuple[datetime, datetime, float], ...]:
    """Turn on/off states into `nameplate × on_fraction` energy (D9 §2, D10 §2).

    The last state is left open: its duration is unknown, and inventing an end
    would put energy in the ledger that nothing measured.
    """
    out: list[tuple[datetime, datetime, float]] = []
    for (at, state), (following, _) in pairwise(rows):
        if state.lower() in ON_STATES:
            hours = (following - at).total_seconds() / 3600.0
            out.append((at, following, nameplate_w * hours / 1000.0))
    return tuple(out)


def _load_history(
    connection: sqlite3.Connection,
    series: Mapping[str, _Series],
    spec: LoadSpec,
    *,
    start: datetime | None,
    end: datetime | None,
) -> tuple[LoadHistory, str | None]:
    """Read one load, preferring energy, then power, then on/off states."""
    found = series.get(spec.entity)
    if found is not None and found.unit in ENERGY_UNITS:
        rows = _read_energy_series(connection, found, start=start, end=end)
        buckets = tuple((a[0], b[0], max(b[1] - a[1], 0.0)) for a, b in pairwise(rows))
        return LoadHistory(spec, "energy", buckets), None
    if found is not None and found.unit in POWER_UNITS:
        rows_p = _read_power_series(connection, found, start=start, end=end)
        buckets = tuple(
            (at, at + timedelta(seconds=span), watts * span / 3.6e6) for at, span, watts in rows_p
        )
        return LoadHistory(spec, "power", buckets), None
    if found is None and spec.entity.startswith("climate."):
        return LoadHistory(spec, "missing"), (
            f"load {spec.load_id}: {spec.entity} has no power or energy statistic, and a climate "
            "entity's state is its mode, not the element's duty — nothing is reconstructed"
        )
    states = _read_states(connection, spec.entity, start=start, end=end)
    if states and spec.nameplate_w:
        return LoadHistory(spec, "on_fraction", _on_buckets(states, spec.nameplate_w)), (
            f"load {spec.load_id} has only on/off states: "
            f"{spec.nameplate_w:.0f} W × on_fraction (D9 §2)"
        )
    if states:
        return LoadHistory(spec, "missing"), (
            f"load {spec.load_id} has on/off states but no nameplate: no energy reconstructed"
        )
    return LoadHistory(spec, "missing"), f"load {spec.load_id} has no history in {spec.entity}"


def _anchor(
    rows: Sequence[tuple[datetime, float]],
    power: Sequence[tuple[datetime, float, float]],
    cadence: float,
) -> tuple[Anchor, str]:
    """Decide what instant a statistics row's `sum` refers to, from the data.

    A statistics row is filed under its period's start, but its `sum` is the last
    value the sensor reported inside that period. For a sensor that updates all
    the time that is the period's **end**; for a latched Norwegian AMS register,
    whose single report at HH:00:12 carries the value *at* HH, it is the period's
    **start** - and then HA's own hourly energy is an hour late (D3 §5.5).

    So the tool measures it: the two candidate shifts are scored against the
    power history's energy over the same windows and the closer one wins. Without
    a power history there is nothing to measure against and `end` is assumed,
    which the note says out loud.
    """
    if len(rows) < ANCHOR_MIN_ROWS or not power:
        return "end", (
            "register anchor assumed `end` (a row's sum is the register at the end of its "
            "period): no power history to check it against"
        )
    scores: dict[Anchor, float] = {}
    candidates: tuple[tuple[Anchor, float], ...] = (("start", 0.0), ("end", cadence))
    for candidate, shift in candidates:
        error = 0.0
        counted = 0
        for (at_a, value_a), (at_b, value_b) in pairwise(rows[:ANCHOR_SAMPLE]):
            first = at_a + timedelta(seconds=shift)
            second = at_b + timedelta(seconds=shift)
            measured = _power_window(power, first, second)
            if measured is None:
                continue
            error += abs((value_b - value_a) - measured)
            counted += 1
        if counted:
            scores[candidate] = error / counted
    if not scores:
        return "end", (
            "register anchor assumed `end`: the power history does not overlap the register"
        )
    best = min(scores, key=lambda key: scores[key])
    detail = ", ".join(f"{key} {value:.3f}" for key, value in sorted(scores.items()))
    return best, (
        f"register anchor `{best}` chosen against the power history: mean |error| per window "
        f"in kWh — {detail}"
    )


def _site_register(
    connection: sqlite3.Connection,
    series: Mapping[str, _Series],
    entity: str,
    window: tuple[datetime | None, datetime | None],
    notes: list[str],
) -> list[tuple[datetime, float]]:
    """Read the site's import register, noting when the database has none."""
    found = series.get(entity)
    if found is None:
        notes.append(f"no rows for {entity}: it is not in statistics_meta")
        return []
    rows = _read_energy_series(connection, found, start=window[0], end=window[1])
    if not rows:
        notes.append(f"no rows for {entity} in statistics or statistics_short_term")
    return rows


def _site_power(
    connection: sqlite3.Connection,
    series: Mapping[str, _Series],
    entity: str | None,
    window: tuple[datetime | None, datetime | None],
    notes: list[str],
) -> list[tuple[datetime, float, float]]:
    """Read the site's grid power, noting when the database has none."""
    if entity is None:
        return []
    found = series.get(entity)
    if found is None:
        notes.append(f"no rows for {entity}: it is not in statistics_meta")
        return []
    rows = _read_power_series(connection, found, start=window[0], end=window[1])
    if not rows:
        notes.append(f"no rows for {entity} in statistics or statistics_short_term")
    return rows


def _anchored(
    register: list[tuple[datetime, float]],
    power: Sequence[tuple[datetime, float, float]],
    anchor: Anchor | Literal["auto"],
) -> tuple[list[tuple[datetime, float]], str]:
    """Shift the register rows to the instant their value refers to (`_anchor`)."""
    if not register:
        return register, ""
    cadence = _cadence_s([at.timestamp() for at, _ in register])
    if anchor == "auto":
        anchor, note = _anchor(register, power, cadence)
    else:
        note = f"register anchor `{anchor}` from --register-anchor"
    if anchor == "end":
        register = [(at + timedelta(seconds=cadence), value) for at, value in register]
    return register, note


def read_recorder(
    path: Path,
    *,
    register: str,
    power: str | None = None,
    loads: Sequence[LoadSpec] = (),
    start: datetime | None = None,
    end: datetime | None = None,
    anchor: Anchor | Literal["auto"] = "auto",
) -> SiteHistory:
    """Read a recorder **copy** read-only and return the site's raw history.

    Long-term statistics only: raw `states` are purged within days, so the
    durable source is `statistics` (hourly) and `statistics_short_term` (5 min).
    The `states` table is read for one thing - a load that never had a power
    sensor, only a switch.
    """
    notes: list[str] = []
    margin = timedelta(hours=2)
    window = (
        None if start is None else start - margin,
        None if end is None else end + margin,
    )
    connection = _connect(path)
    try:
        series = _series(connection)
        register_rows = _site_register(connection, series, register, window, notes)
        power_rows = _site_power(connection, series, power, window, notes)
        histories: list[LoadHistory] = []
        for spec in loads:
            history, note = _load_history(connection, series, spec, start=start, end=end)
            histories.append(history)
            if note:
                notes.append(note)
    finally:
        connection.close()

    register_rows, note = _anchored(register_rows, power_rows, anchor)
    if note:
        notes.append(note)
    return SiteHistory(
        register=tuple(register_rows),
        power=tuple(power_rows),
        loads=tuple(histories),
        source=f"recorder {path.name}",
        tz_name=_ha_time_zone(path),
        requested=(start, end),
        notes=tuple(notes),
    )


def _ha_time_zone(path: Path) -> str | None:
    """Return the zone from a `.storage/core.config` beside the database, if any.

    The recorder database itself stores no time zone - every timestamp in it is a
    UTC epoch - so the only zone a copy can carry is the one in the configuration
    directory it came from.
    """
    config = path.parent / ".storage" / "core.config"
    if not config.is_file():
        return None
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
        zone = raw.get("data", {}).get("time_zone")
    except OSError, ValueError, AttributeError:
        return None
    return str(zone) if zone else None


# --------------------------------------------------------------------------- #
# the CSV export
# --------------------------------------------------------------------------- #


def _csv_rows(path: Path) -> list[tuple[datetime, str]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    out: list[tuple[datetime, str]] = []
    for line in lines[1:]:  # the header is documented, not parsed
        stamp, _, value = line.partition(",")
        out.append((_parse_instant(stamp.strip()), value.strip()))
    out.sort(key=lambda row: row[0])
    return out


def _parse_instant(text: str) -> datetime:
    at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError(f"{text!r} has no UTC offset: a backtest never guesses a zone")
    return at.astimezone(UTC)


def read_csv(
    directory: Path,
    *,
    loads: Sequence[LoadSpec] = (),
    start: datetime | None = None,
    end: datetime | None = None,
) -> SiteHistory:
    """Read the documented CSV layout and return the site's raw history."""
    notes: list[str] = []
    meta: dict[str, Any] = {}
    meta_path = directory / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    register: list[tuple[datetime, float]] = []
    register_path = directory / "grid_register.csv"
    if register_path.is_file():
        register = [(at, float(value)) for at, value in _csv_rows(register_path)]
    else:
        notes.append(f"no rows for the register: {register_path.name} is not in {directory}")

    power: list[tuple[datetime, float, float]] = []
    power_path = directory / "grid_power.csv"
    if power_path.is_file():
        rows = [(at, float(value)) for at, value in _csv_rows(power_path)]
        cadence = _cadence_s([at.timestamp() for at, _ in rows])
        power = [(at, cadence, watts) for at, watts in rows]

    histories: list[LoadHistory] = []
    for spec in loads:
        history, note = _csv_load(directory / "loads", spec)
        histories.append(history)
        if note:
            notes.append(note)

    return SiteHistory(
        register=tuple(register),
        power=tuple(power),
        loads=tuple(histories),
        source=f"csv {directory.name}",
        tz_name=meta.get("tz"),
        window_min=meta.get("window_min"),
        requested=(start, end),
        notes=tuple(notes),
    )


def _csv_load(directory: Path, spec: LoadSpec) -> tuple[LoadHistory, str | None]:
    """Read one load from `loads/<id>.{energy,power,onoff}.csv`."""
    energy = directory / f"{spec.load_id}.energy.csv"
    if energy.is_file():
        rows = [(at, float(value)) for at, value in _csv_rows(energy)]
        buckets = tuple((a[0], b[0], max(b[1] - a[1], 0.0)) for a, b in pairwise(rows))
        return LoadHistory(spec, "energy", buckets), None
    power = directory / f"{spec.load_id}.power.csv"
    if power.is_file():
        rows = [(at, float(value)) for at, value in _csv_rows(power)]
        cadence = _cadence_s([at.timestamp() for at, _ in rows])
        buckets = tuple(
            (at, at + timedelta(seconds=cadence), watts * cadence / 3.6e6) for at, watts in rows
        )
        return LoadHistory(spec, "power", buckets), None
    onoff = directory / f"{spec.load_id}.onoff.csv"
    if onoff.is_file() and spec.nameplate_w:
        states = [(at, value) for at, value in _csv_rows(onoff)]
        return LoadHistory(spec, "on_fraction", _on_buckets(states, spec.nameplate_w)), (
            f"load {spec.load_id} has only on/off states: "
            f"{spec.nameplate_w:.0f} W × on_fraction (D9 §2)"
        )
    return LoadHistory(spec, "missing"), f"load {spec.load_id} has no CSV under {directory}"


# --------------------------------------------------------------------------- #
# the reconstruction
# --------------------------------------------------------------------------- #


def _covered(instants: Sequence[float], at: datetime, tolerance: float) -> bool:
    """Whether a register row sits within `tolerance` of this boundary."""
    target = at.timestamp()
    index = _bisect(instants, target)
    for candidate in instants[max(index - 1, 0) : index + 1]:
        if abs(candidate - target) <= tolerance:
            return True
    return False


def _bisect(values: Sequence[float], target: float) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _power_window(
    rows: Sequence[tuple[datetime, float, float]], start: datetime, end: datetime
) -> float | None:
    """Integrate the power rows over `[start, end)`, or `None` if they do not cover it."""
    want = (end - start).total_seconds()
    covered = 0.0
    kwh = 0.0
    for at, span, watts in rows:
        row_end = at + timedelta(seconds=span)
        if row_end <= start or at >= end:
            continue
        overlap = (min(row_end, end) - max(at, start)).total_seconds()
        if overlap <= 0.0:
            continue
        covered += overlap
        kwh += watts * overlap / 3.6e6
    return kwh if covered >= want * POWER_COVER else None


def reconstruct(
    history: SiteHistory, window_min: int, tz: tzinfo
) -> tuple[tuple[Window, ...], tuple[str, ...]]:
    """Rebuild the site's windows from the register, then from power (D3 §5.11).

    `reconstruct_windows` interpolates across a hole, which conserves energy but
    smears a peak over the hours either side - for a capacity tariff that is a
    silent understatement. So every window it returns is checked against the rows
    that actually exist: one whose boundaries were not measured is replaced from
    the power history or dropped, and the count of drops goes in a note.
    """
    notes: list[str] = []
    register = sorted(history.register, key=lambda row: row[0])
    instants = [at.timestamp() for at, _ in register]
    cadence = _cadence_s(instants) if len(instants) > 1 else float(window_min) * 60.0
    tolerance = max(BOUNDARY_TOLERANCE_S, cadence * BOUNDARY_TOLERANCE_CADENCES)

    from_register = reconstruct_windows(register, window_min, tz)
    measured = {
        closed.start_utc: closed
        for closed in from_register
        if _covered(instants, closed.start_utc, tolerance)
        and _covered(instants, closed.start_utc + timedelta(minutes=closed.window_min), tolerance)
    }
    effective = (
        Counter(closed.window_min for closed in from_register).most_common(1)[0][0]
        if from_register
        else window_min
    )
    if effective > window_min:
        notes.append(
            f"the register reports every {cadence:.0f} s: hourly statistics cannot make "
            f"{window_min} min windows, so D2 is fed {effective} min windows and marks them "
            "coarse (D3 §5.11, D2 §5.1)"
        )

    first, last = _span(history, register, effective)
    if first is None or last is None:
        notes.append("no window could be reconstructed: the history has no rows to read")
        return (), tuple(notes)

    out: list[Window] = []
    missing: list[datetime] = []
    start = window_bounds(first, effective, tz)[0]
    if start < first:
        start = window_bounds(first + timedelta(minutes=effective), effective, tz)[0]
    while start < last:
        end = window_bounds(start + timedelta(minutes=effective), effective, tz)[0]
        if end > last:
            break
        closed = measured.get(start)
        if closed is not None:
            out.append(Window(closed, "register"))
        else:
            kwh = _power_window(history.power, start, end)
            if kwh is None:
                missing.append(start)
            else:
                minutes = round((end - start).total_seconds() / 60.0)
                out.append(
                    Window(
                        ClosedWindow(
                            start_utc=start,
                            window_min=minutes,
                            kwh=kwh,
                            avg_kw=kwh / (minutes / 60.0),
                            anchor_kind=AnchorKind.WALL_CLOCK,
                            degraded=False,
                            confidence="estimated",
                        ),
                        "power",
                    )
                )
        start = end
    if missing:
        notes.append(
            f"{len(missing)} window{'s' if len(missing) != 1 else ''} had neither a register row "
            "at both boundaries nor power cover: dropped, not interpolated (PLAN §6 R8); first "
            f"at {missing[0].isoformat()}, last at {missing[-1].isoformat()}"
        )
    return tuple(out), tuple(notes)


def _span(
    history: SiteHistory,
    register: Sequence[tuple[datetime, float]],
    effective: int,
) -> tuple[datetime | None, datetime | None]:
    """Return the instants the history can speak for, clipped to what was asked."""
    firsts = [at for at, _ in register[:1]]
    lasts = [at for at, _ in register[-1:]]
    if history.power:
        firsts.append(history.power[0][0])
        lasts.append(history.power[-1][0] + timedelta(seconds=history.power[-1][1]))
    if not firsts:
        return None, None
    first, last = min(firsts), max(lasts)
    want_start, want_end = history.requested
    if want_start is not None:
        first = max(first, want_start.astimezone(UTC))
    if want_end is not None:
        last = min(last, want_end.astimezone(UTC))
    return (first, last) if last > first else (None, None)


# --------------------------------------------------------------------------- #
# the bill
# --------------------------------------------------------------------------- #


def _billed(window: Window, tariff_min: int) -> list[tuple[ClosedWindow, bool]]:
    """Split or keep a window as D2 records it: `(window, coarse)` (D2 §5.1)."""
    if window.closed.window_min > tariff_min and window.closed.window_min % tariff_min == 0:
        parts = window.closed.window_min // tariff_min
        return [
            (
                ClosedWindow(
                    start_utc=window.closed.start_utc + timedelta(minutes=tariff_min * index),
                    window_min=tariff_min,
                    kwh=window.closed.kwh / parts,
                    avg_kw=window.closed.avg_kw,
                    anchor_kind=window.closed.anchor_kind,
                    degraded=window.closed.degraded,
                    confidence=window.closed.confidence,
                ),
                True,
            )
            for index in range(parts)
        ]
    return [(window.closed, False)]


def _tariff_at(spec: TariffSpec, at: datetime) -> PeakTariff | None:
    return spec.version_at(at).peak


def _target_kw(spec: TariffSpec, period_end: datetime, metric_kw: float, target: Target) -> float:
    """Return the kW this period is judged against (D2 §5.5's `resolve_target_kw`)."""
    tariff = _tariff_at(spec, period_end - timedelta(microseconds=1))
    if tariff is None:
        return math.inf
    return resolve_target_kw(
        target,
        pricing=tariff.pricing,
        metric_kw=metric_kw,
        partial=False,
        previous_kw=0.0,
    )


def _reconstruction(loads: Sequence[LoadHistory]) -> Reconstruction:
    """`full` when every load was measured, `none` when none was (D9 §2)."""
    if not loads:
        return "none"
    qualities = [load.quality for load in loads]
    if all(quality in {"energy", "power"} for quality in qualities):
        return "full"
    if all(quality == "missing" for quality in qualities):
        return "none"
    return "partial"


def _calendar(spec: TariffSpec) -> HolidayCalendar:
    """Return the site's holiday calendar, or none at all with a warning."""
    if spec.country:
        try:
            return calendar_for(spec.country)
        except UnknownCalendarError:
            _LOGGER.warning(
                "no holiday calendar for %s; treating every day as ordinary", spec.country
            )
    return NoHolidays()


def run(
    history: SiteHistory,
    spec: TariffSpec,
    tz: tzinfo,
    *,
    window_min: int | None = None,
    target: Target = AUTO,
    calendar: HolidayCalendar | None = None,
    tz_source: str = "",
) -> BacktestResult:
    """Reconstruct, bill period by period, and report (D9 §5.4)."""
    tariff = spec.versions[-1].peak
    asked_min = window_min or history.window_min or (tariff.window_min if tariff else None)
    asked_min = asked_min or FALLBACK_WINDOW_MIN

    windows, notes = reconstruct(history, asked_min, tz)
    evaluator = Evaluator(
        spec, tz=tz, calendar=calendar if calendar is not None else _calendar(spec), target=target
    )

    billed: dict[str, list[tuple[ClosedWindow, bool, Origin]]] = {}
    for window in windows:
        tariff_min = _tariff_min(spec, window.closed.start_utc, asked_min)
        for closed, coarse in _billed(window, tariff_min):
            key = evaluator.period(closed.start_utc).key
            billed.setdefault(key, []).append((closed, coarse, window.origin))
    seed_from_windows(evaluator, [window.closed for window in windows])

    months: dict[str, BacktestMetrics] = {}
    for key in sorted(billed):
        rows = billed[key]
        period = evaluator.period(rows[0][0].start_utc)
        bill = evaluator.bill(period)
        target_kw = _target_kw(spec, period.end, bill.metric_kw, target)
        over = sum(
            1
            for closed, _, _ in rows
            if evaluator.weight_now(closed.start_utc) * closed.avg_kw > target_kw
        )
        load_kwh = {
            load.spec.load_id: total
            for load in history.loads
            if (total := _load_total(load, period, tz)) is not None
        }
        months[key] = BacktestMetrics(
            windows=len(rows),
            over_target=over,
            max_window_kwh=max(closed.kwh for closed, _, _ in rows),
            level_reached=bill.level.name,
            fee=bill.capacity_fee,
            metric_kw=bill.metric_kw,
            target_kw=target_kw,
            gate=over == 0,
            confidence=bill.level.confidence,
            coarse_windows=sum(1 for _, coarse, _ in rows if coarse),
            estimated_windows=sum(1 for _, _, origin in rows if origin == "power"),
            days=len({closed.start_utc.astimezone(tz).date() for closed, _, _ in rows}),
            reconstruction=_reconstruction(history.loads),
            load_kwh=load_kwh,
            load_quality={load.spec.load_id: load.quality for load in history.loads},
        )

    notes = (*history.notes, *notes, _MONEY_NOTE)
    return BacktestResult(
        preset=spec.id,
        tz_name=str(tz),
        tz_source=tz_source,
        window_min=asked_min,
        target=_target_text(target),
        months=months,
        total=_total(months, history, spec),
        windows=windows,
        span=(windows[0].closed.start_utc, _end_of(windows[-1].closed)) if windows else None,
        notes=notes,
        source=history.source,
    )


_MONEY_NOTE = (
    "cost_energy, cost_counterfactual and savings are None: the energy side is D11's "
    "Accounting and lands with WP0.10, the controller comparison with WP0.9's --simulate "
    "(D9 §5.4)"
)


def _end_of(closed: ClosedWindow) -> datetime:
    return closed.start_utc + timedelta(minutes=closed.window_min)


def _tariff_min(spec: TariffSpec, at: datetime, fallback: int) -> int:
    tariff = _tariff_at(spec, at)
    return tariff.window_min if tariff else fallback


def _load_total(load: LoadHistory, period: Any, tz: tzinfo) -> float | None:
    """Return the load's kWh inside one period, or `None` when it has no rows there."""
    total = 0.0
    seen = False
    for start, end, kwh in load.buckets:
        if start >= period.end or end <= period.start:
            continue
        seen = True
        span = (end - start).total_seconds()
        overlap = (min(end, period.end) - max(start, period.start)).total_seconds()
        total += kwh * (overlap / span if span > 0 else 1.0)
    return total if seen else None


def _total(
    months: Mapping[str, BacktestMetrics], history: SiteHistory, spec: TariffSpec
) -> BacktestMetrics:
    """Sum what sums; for the rest report the worst month, never an average."""
    if not months:
        return BacktestMetrics(
            windows=0,
            over_target=0,
            max_window_kwh=0.0,
            level_reached="none",
            fee=Money(Decimal(0), spec.currency),
            metric_kw=0.0,
            target_kw=math.inf,
            gate=True,
            confidence="partial",
            coarse_windows=0,
            estimated_windows=0,
            days=0,
            reconstruction=_reconstruction(history.loads),
            load_quality={load.spec.load_id: load.quality for load in history.loads},
        )
    rows = list(months.values())
    peak = max(rows, key=lambda row: row.metric_kw)
    load_kwh: dict[str, float] = {}
    for row in rows:
        for load_id, kwh in row.load_kwh.items():
            load_kwh[load_id] = load_kwh.get(load_id, 0.0) + kwh
    order = {"exact": 0, "partial": 1, "coarse": 2}
    return BacktestMetrics(
        windows=sum(row.windows for row in rows),
        over_target=sum(row.over_target for row in rows),
        max_window_kwh=max(row.max_window_kwh for row in rows),
        level_reached=peak.level_reached,
        fee=Money(sum((row.fee.amount for row in rows), Decimal(0)), rows[0].fee.currency),
        metric_kw=peak.metric_kw,
        target_kw=peak.target_kw,
        gate=all(row.gate for row in rows),
        confidence=max((row.confidence for row in rows), key=lambda value: order.get(value, 0)),
        coarse_windows=sum(row.coarse_windows for row in rows),
        estimated_windows=sum(row.estimated_windows for row in rows),
        days=sum(row.days for row in rows),
        reconstruction=_reconstruction(history.loads),
        load_kwh=load_kwh,
        load_quality={load.spec.load_id: load.quality for load in history.loads},
    )


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def _kw(value: float) -> str:
    return "open" if math.isinf(value) else f"{value:.2f}"


def render_table(result: BacktestResult) -> str:
    """Render the plain-text report: a header, one row per period, then the notes."""
    head = [
        f"powerplan backtest · {result.source}",
        (
            f"preset {result.preset} · window {result.window_min} min · "
            f"tz {result.tz_name} (from {result.tz_source}) · target {result.target}"
        ),
    ]
    if result.span is not None:
        head.append(
            f"span {result.span[0].isoformat()} → {result.span[1].isoformat()} "
            f"({result.total.days} days, {result.total.windows} windows)"
        )
    columns = (
        f"{'period':<8} {'windows':>7} {'coarse':>6} {'est':>5} {'max kWh':>8} {'metric kW':>9} "
        f"{'level':<12} {'target kW':>9} {'over':>5} {'gate':>5} {'recon':>8} {'fee':>12}"
    )
    lines = [*head, "", columns, "-" * len(columns)]
    for key, row in [*result.months.items(), ("total", result.total)]:
        lines.append(
            f"{key:<8} {row.windows:>7} {row.coarse_windows:>6} {row.estimated_windows:>5} "
            f"{row.max_window_kwh:>8.2f} {row.metric_kw:>9.2f} {row.level_reached:<12} "
            f"{_kw(row.target_kw):>9} {row.over_target:>5} {'yes' if row.gate else 'NO':>5} "
            f"{row.reconstruction:>8} {row.fee.amount!s:>8} {row.fee.currency:<3}"
        )
    if result.total.load_kwh:
        lines += ["", "per load (kWh over the whole span)"]
        lines += [
            f"  {load_id:<24} {kwh:>10.1f}  {result.total.load_quality.get(load_id, '')}"
            for load_id, kwh in sorted(result.total.load_kwh.items())
        ]
    missing = [
        load_id
        for load_id, quality in result.total.load_quality.items()
        if load_id not in result.total.load_kwh or quality == "missing"
    ]
    if missing:
        lines.append(f"  no history: {', '.join(sorted(missing))}")
    lines += ["", "notes"]
    lines += [f"  - {note}" for note in result.notes]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #


def parse_loads(raw: str) -> tuple[LoadSpec, ...]:
    """Parse `--loads`: a JSON file, or `id=entity[:type[:nameplate_w]]` items.

    ```
    --loads loads.json
    --loads "ev=sensor.garasje_billader_power:ev:11000,tank=switch.bereder:water_heater:2000"
    ```
    """
    path = Path(raw)
    if path.suffix == ".json" or path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["loads"] if isinstance(data, dict) else data
        return tuple(
            LoadSpec(
                load_id=str(row["id"]),
                kind=str(row.get("type", "")),
                entity=str(row["entity"]),
                nameplate_w=None if row.get("nameplate_w") is None else float(row["nameplate_w"]),
            )
            for row in rows
        )
    out: list[LoadSpec] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        load_id, _, rest = item.strip().partition("=")
        if not rest:
            raise ValueError(f"{item!r} is not `id=entity[:type[:nameplate_w]]`")
        entity, kind, nameplate = (*rest.split(":"), "", "")[:3]
        out.append(
            LoadSpec(
                load_id=load_id.strip(),
                kind=kind,
                entity=entity,
                nameplate_w=float(nameplate) if nameplate else None,
            )
        )
    return tuple(out)


def parse_target(raw: str) -> Target:
    """Parse `--target`: `auto`, `step:<n>` or a number of kW."""
    if raw == "auto":
        return AUTO
    if raw.startswith("step:"):
        return Target("step", step_index=int(raw.removeprefix("step:")))
    return Target("kw", kw=float(raw))


def _target_text(target: Target) -> str:
    if target.kind == "auto":
        return "auto (the step the history itself reached)"
    if target.kind == "step":
        return f"step {target.step_index}"
    return f"{target.kw} kW"


def load_tariff(path: Path) -> tuple[TariffSpec, str | None]:
    """Return the spec a tariff file holds and the zone it names, if it names one.

    A stored copy (`HouseholdPrice` JSON, or the `tariff` section around it) is
    priced as the household pays it; a rule-format file as it is written.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise SystemExit(f"--tariff {path}: {err}") from err
    if isinstance(raw.get("price"), dict):
        raw = raw["price"]
    try:
        if "grid" in raw:
            return price_spec(price_from_json(raw)), None
        zone = raw.get("tz")
        return spec_from_raw(raw, source=str(path)), str(zone) if zone else None
    except (PresetError, KeyError, ValueError) as err:
        raise SystemExit(f"--tariff {path}: {err}") from err


def resolve_tz(
    flag: str | None, history: SiteHistory, tariff_zone: str | None, tariff: str
) -> tuple[tzinfo, str]:
    """Return the zone and the name of the source it came from - never a default."""
    for name, source in (
        (flag, "--tz"),
        (history.tz_name, f"the {history.source.split()[0]} metadata"),
        (tariff_zone, f"the tariff file {tariff}"),
    ):
        if name:
            try:
                return ZoneInfo(name), source
            except ZoneInfoNotFoundError as err:
                raise SystemExit(f"{name!r} is not a time zone this system knows") from err
    raise SystemExit(
        "no time zone: the recorder database stores none, the export carries none and "
        f"the tariff {tariff} names none. Pass --tz <IANA zone>."
    )


def _local(text: str, tz: tzinfo) -> datetime:
    at = datetime.fromisoformat(text)
    return at.replace(tzinfo=tz) if at.tzinfo is None else at


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (D9 §6)."""
    parser = argparse.ArgumentParser(
        prog="tools/backtest.py",
        description=(
            "Replay recorder or CSV history through D3's window reconstruction and D2's "
            "bill, and report what it would have cost. Reports; never judges."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("**The CSV layout.**")[1].split("**The zone")[0].strip(),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--recorder", type=Path, help="a COPY of home-assistant_v2.db, opened read-only"
    )
    source.add_argument("--csv", type=Path, help="a directory in the layout above")
    source.add_argument(
        "--simulate",
        metavar="SCENARIO",
        help=(
            "run a catalogue scenario (tests/scenarios/catalogue.py) through the same engine "
            "on the simulated house and report its metrics (D9 §5.2, §5.4)"
        ),
    )
    parser.add_argument(
        "--tariff", type=Path, help="a stored copy or a rule-format file (JSON), e.g. a fixture"
    )
    parser.add_argument("--register", help="the grid import register entity (recorder mode)")
    parser.add_argument("--power", help="the grid power entity, for windows the register misses")
    parser.add_argument(
        "--register-anchor",
        default="auto",
        choices=("auto", "start", "end"),
        help="what a statistics row's sum refers to; auto measures it against --power",
    )
    parser.add_argument("--loads", default="", help="id=entity[:type[:nameplate_w]],… or a .json")
    parser.add_argument("--from", dest="start", help="local date or datetime to start at")
    parser.add_argument("--to", dest="end", help="local date or datetime to stop at")
    parser.add_argument("--tz", help="IANA zone; overrides every other source")
    parser.add_argument("--window-min", type=int, help="override the tariff's window length")
    parser.add_argument("--target", default="auto", help="auto | step:<n> | <kw>")
    parser.add_argument("--out", type=Path, help="write the full result as JSON here")
    parser.add_argument("-v", "--verbose", action="store_true", help="log at DEBUG")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one backtest and print its report."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    if args.simulate is not None:
        return simulate(args.simulate, out=args.out)
    if not args.tariff:
        raise SystemExit("--recorder and --csv need --tariff <file> (the tariff)")
    spec, tariff_zone = load_tariff(args.tariff)

    loads = parse_loads(args.loads) if args.loads else ()
    target = parse_target(args.target)

    if args.recorder is not None:
        if not args.register:
            raise SystemExit("--recorder needs --register <entity id> (the import register)")
        if not args.recorder.is_file():
            raise SystemExit(f"{args.recorder} is not a file")
        probe = read_recorder(args.recorder, register=args.register)
        tz, tz_source = resolve_tz(args.tz, probe, tariff_zone, str(args.tariff))
        start = _local(args.start, tz) if args.start else None
        end = _local(args.end, tz) if args.end else None
        history = read_recorder(
            args.recorder,
            register=args.register,
            power=args.power,
            loads=loads,
            start=start,
            end=end,
            anchor=args.register_anchor,
        )
    else:
        probe = read_csv(args.csv)
        tz, tz_source = resolve_tz(args.tz, probe, tariff_zone, str(args.tariff))
        start = _local(args.start, tz) if args.start else None
        end = _local(args.end, tz) if args.end else None
        history = read_csv(args.csv, loads=loads, start=start, end=end)

    if start is None and end is None:
        history = _last_twelve_months(history)

    result = run(history, spec, tz, window_min=args.window_min, target=target, tz_source=tz_source)
    print(render_table(result))
    if args.out is not None:
        args.out.write_text(json.dumps(result.as_dict(), indent=2), encoding="utf-8")
        _LOGGER.info("wrote %s", args.out)
    return 0


def simulate(name: str, *, out: Path | None = None) -> int:
    """Run one catalogue scenario on the simulated house and print its metrics (D9 §5.4).

    The scenarios and their simulators live in the test tree (D9 §3), so this
    imports them from the repository root; the engine they drive is the shipped
    one. The house carries its own tariff, so `--tariff` does not apply.
    """
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests.scenarios import catalogue  # noqa: PLC0415 - the test tree is optional at import
    from tests.scenarios.runner import run_scenario  # noqa: PLC0415

    factory = getattr(catalogue, name, None)
    if factory is None or not callable(factory):
        names = ", ".join(scenario.__name__ for scenario in catalogue.PHASE0)
        raise SystemExit(f"--simulate {name}: not a catalogue scenario; one of {names}")
    result = run_scenario(factory())
    metrics = result.as_dict()
    print(f"scenario {name}")
    for key in (
        "ticks",
        "plans",
        "windows",
        "over_target",
        "max_window_kwh",
        "comfort_violation_min",
        "bathroom_min_c",
        "ev_soc_at_departure",
        "deadline_misses",
        "tank_top_at_ready",
        "ev_stops",
        "commitment_breaks",
        "plan_gaps",
        "frozen_ticks",
        "engine_failures",
        "cost_energy",
        "cost_counterfactual",
        "savings",
        "savings_confidence",
    ):
        print(f"  {key:<24} {metrics.get(key)}")
    for load_id, count in sorted(metrics["writes"].items()):
        print(
            f"  writes {load_id:<17} {count} (max {metrics['max_writes_per_10min'][load_id]}/10 min)"
        )
    if out is not None:
        out.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
        _LOGGER.info("wrote %s", out)
    return 0


def _last_twelve_months(history: SiteHistory) -> SiteHistory:
    """Clip an unbounded run to the last twelve months the source holds (D9 §5.4)."""
    ends = [at for at, _ in history.register[-1:]]
    if history.power:
        ends.append(history.power[-1][0])
    if not ends:
        return history
    end = max(ends)
    return SiteHistory(
        register=history.register,
        power=history.power,
        loads=history.loads,
        source=history.source,
        tz_name=history.tz_name,
        window_min=history.window_min,
        requested=(end - timedelta(days=365), end),
        notes=(*history.notes, "no --from/--to: clipped to the last 365 days present"),
    )


if __name__ == "__main__":
    sys.exit(main())
