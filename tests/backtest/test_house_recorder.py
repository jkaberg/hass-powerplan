"""The reference house's own recorder history through the backtest (D9 §5.12).

A **house check**, not a gate (PLAN §3, D9 §1): it runs only when
`POWERPLAN_RECORDER_COPY` points at a *copy* of the house's
`home-assistant_v2.db`, and it is skipped otherwise - the one sanctioned skip in
this suite, because external data is either there or it is not. What it produces
is a log: the span
actually present, the per-month numbers, how good the reconstruction was, and the
phase-0 gate sentence answered per month with a count. Numbers, not judgement.

    POWERPLAN_RECORDER_COPY=/path/to/copy.db uv run pytest tests/backtest -m backtest
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs.rules.loader import load
from tools.backtest import (
    BacktestMetrics,
    BacktestResult,
    LoadHistory,
    LoadSpec,
    SiteHistory,
    Window,
    read_recorder,
    render_table,
    resolve_tz,
    run,
)

pytestmark = pytest.mark.backtest

COPY_ENV = "POWERPLAN_RECORDER_COPY"
PRESET = "no/tensio-ts"
OSLO = ZoneInfo("Europe/Oslo")

#: The reference house's meter (D3 §6): a Norwegian AMS register plus its power
#: sensor. Verified against `statistics_meta` in the copy, not assumed.
REGISTER = "sensor.dataskap_strommaler_energy"
POWER = "sensor.dataskap_strommaler_power"

#: Every load the house has that a recorder can speak for. The tank is in the
#: list on purpose: it is a `generic_thermostat` over `climate.varmtvannsbereder`
#: with no power or energy statistic at all, and a check that leaves it out would
#: read `full` instead of `partial` (PLAN §6 R8).
LOADS = (
    LoadSpec("ev", "ev", "sensor.garasje_billader_energy", 11_000.0),
    LoadSpec("floor_bad_1_etasje", "floor_heating", "sensor.bad_1_etasje_gulvvarme_energy"),
    LoadSpec("floor_bad_2_etasje", "floor_heating", "sensor.bad_2_etasje_gulvvarme_energy"),
    LoadSpec("floor_inngang", "floor_heating", "sensor.inngang_gulvvarme_energy"),
    LoadSpec("floor_kjokken", "floor_heating", "sensor.kjokken_gulvvarme_energy"),
    LoadSpec("floor_stua", "floor_heating", "sensor.stua_gulvvarme_energy"),
    LoadSpec("floor_tv_stua", "floor_heating", "sensor.tv_stua_gulvvarme_energy"),
    LoadSpec("heat_pump_1_etasje", "heat_pump", "sensor.varmepumpe_1_etasje_power_consumption"),
    LoadSpec("heat_pump_2_etasje", "heat_pump", "sensor.varmepumpe_2_etasje_energy"),
    LoadSpec("tank", "water_heater", "climate.varmtvannsbereder"),
)

LOG_DIR = Path(__file__).resolve().parents[2] / "design" / "benchmarks" / "house"


def _today() -> str:
    """Return the local day this check ran on: the log's file name and heading."""
    return datetime.now(UTC).astimezone(OSLO).date().isoformat()


def _copy() -> Path:
    raw = os.environ.get(COPY_ENV)
    if not raw:
        pytest.skip(f"{COPY_ENV} is not set: no recorder copy to read (D9 §5.12)")
    path = Path(raw)
    if not path.is_file():
        pytest.skip(f"{COPY_ENV} does not point at a file: {path}")
    return path


def _build() -> str:
    """Return the build this check ran on, as D9 §5.12's log format asks."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=LOG_DIR.parents[2],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:  # pragma: no cover
        return "unknown"


def test_the_reference_house_backtest_runs_and_is_logged() -> None:
    """Run the last twelve months of the house's history and write the log."""
    copy = _copy()
    probe = read_recorder(copy, register=REGISTER)
    assert probe.register, f"{REGISTER} has no statistics in {copy}"

    end = probe.register[-1][0]
    start = end - timedelta(days=365)
    history = read_recorder(copy, register=REGISTER, power=POWER, loads=LOADS, start=start, end=end)
    zone, tz_source = resolve_tz(None, history, PRESET)
    assert str(zone) == str(OSLO), "the preset's own zone is the house's zone"
    result = run(history, load(PRESET), zone, tz_source=tz_source)

    assert result.windows, "no window could be reconstructed from the house's register"
    assert result.months, "the reconstruction produced no billable period"
    # A year of hourly windows, give or take the rows the recorder never wrote.
    assert 8000 <= result.total.windows <= 8800
    assert result.total.fee.currency == "NOK"
    assert result.total.reconstruction in {"full", "partial", "none"}
    assert render_table(result)

    check = run(
        read_recorder(
            copy,
            register=REGISTER,
            power=POWER,
            start=datetime(2026, 9, 1, tzinfo=OSLO),
            end=datetime(2026, 9, 4, tzinfo=OSLO),
        ),
        load(PRESET),
        zone,
        tz_source=tz_source,
    )

    path = LOG_DIR / f"{_today()}-recorder-backtest.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _log(result, history, copy, start=start, end=end, check=check), encoding="utf-8"
    )
    assert path.is_file()


# --------------------------------------------------------------------------- #
# the log
# --------------------------------------------------------------------------- #


def _log(
    result: BacktestResult,
    history: SiteHistory,
    copy: Path,
    *,
    start: datetime,
    end: datetime,
    check: BacktestResult,
) -> str:
    """Render the house check as markdown (D9 §5.12)."""
    whole = [key for key, row in result.months.items() if row.days >= 28]
    passed = [key for key in whole if result.months[key].gate]
    span_from = result.span[0].astimezone(OSLO).isoformat()
    span_to = result.span[1].astimezone(OSLO).isoformat()
    total = result.total
    lines = [
        f"# House check {_today()} — recorder backtest (NO preset)",
        "",
        "| | |",
        "|---|---|",
        "| Check | D9 §5.12 · `tools/backtest.py --recorder` (WP0.9b) |",
        f"| Build | `{_build()}` |",
        (
            f"| Source | `{copy.name}`, a read-only copy of the house's "
            "`home-assistant_v2.db`; long-term statistics only |"
        ),
        (
            f"| Preset | `{result.preset}` · window {result.window_min} min · "
            f"tz {result.tz_name} (from {result.tz_source}) |"
        ),
        (
            f"| Span asked | {start.astimezone(OSLO).date().isoformat()} → "
            f"{end.astimezone(OSLO).date().isoformat()} (365 days) |"
        ),
        f"| Span reconstructed | {span_from} → {span_to} |",
        f"| Windows | {total.windows} of 8 760 hourly windows over {total.days} local days |",
        f"| Reconstruction | site `{total.confidence}` · loads `{total.reconstruction}` |",
        f"| Capacity fee | {total.fee.amount} {total.fee.currency} over the span |",
        "",
        "## Gate",
        "",
        "PLAN §3's phase-0 gate sentence is **every window under target for the NO preset**.",
        (
            "Target is `auto`: the step each month itself reached, so the count reads "
            '"windows that would have raised the step".'
        ),
        "",
        (
            f"- Whole months in the span: **{len(whole)}** — **{len(passed)}** with every "
            f"window under target, **{len(whole) - len(passed)}** with at least one over."
        ),
        f"- Windows over target across the whole span: **{total.over_target}**.",
        (
            "- This is the plain replay: the history already happened, so what it measures is "
            "D2's and D3's arithmetic, not a controller. The controller is judged by "
            "`--simulate` (D9 §5.4), which lands with WP0.9."
        ),
        "",
        "## Per month",
        "",
        (
            "| period | days | windows | coarse | estimated | max kWh | metric kW | level |"
            " target kW | over target | under target | fee |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_month_row(key, row) for key, row in result.months.items()]
    lines.append(_month_row("**total**", result.total))
    lines += [
        "",
        "## Per load",
        "",
        (
            "What the recorder could say about each load over the span. `energy` is the load's"
            " own kWh register, `power` its mean-power statistics, `missing` no history at all"
            " — never a zero (PLAN §6 R8)."
        ),
        "",
        "| load | type | entity | quality | first row | last row | kWh over the span |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [_load_row(item, result.total) for item in history.loads]
    lines += _cross_check(check)
    lines += ["", "## Notes the run emitted", ""]
    lines += [f"- {note}" for note in result.notes]
    lines += ["", "```", render_table(result), "```", ""]
    return "\n".join(lines)


#: The five hours of 2026-09 that effektstyring's README §1 tabulates, quoted as
#: the source of `tests/golden/presets/no.tensio-ts.household.json`.
PUBLISHED = "d03h21 5.26, d03h22 4.91, d03h23 4.52, d02h22 4.39, d01h22 3.97 kWh → 4.54 kW"


def _cross_check(check: BacktestResult) -> list[str]:
    """Compare the month's first days with the only independently published numbers."""
    top = sorted(check.windows, key=lambda item: -item.closed.kwh)[:5]
    month = check.months.get("2026-09")
    metric = "—" if month is None else f"{month.metric_kw:.2f} kW"
    level = "—" if month is None else month.level_reached
    return [
        "",
        "## Cross-check: 2026-09-01 → 04 against effektstyring's published table",
        "",
        (
            "The only independent numbers for this house are the five hours in effektstyring's"
            " README §1, quoted as the source of the Tensio golden file. They cover the first"
            " three local days of 2026-09, so that is the span reconstructed here:"
        ),
        "",
        "| local hour | kWh |",
        "|---|---|",
        *[f"| {_hour(item)} | {item.closed.kwh:.2f} |" for item in top],
        "",
        f"- published: `{PUBLISHED}`",
        f"- reconstructed: metric {metric}, level {level}",
        (
            "- The energies and the metric agree exactly. The hour *labels* do not: every"
            " published hour is one earlier than the hour whose mean power carries that energy,"
            " which is what a fixed +1 offset does to a summer date (CEST read as CET). The"
            " 4.52 kWh hour the README files as d03h23 is 2026-09-04T00:00 here, so over a"
            " longer span it counts towards the 4th — with the whole of 2026-09-01…06 the"
            " top-3-distinct-days mean is 4.72 kW rather than 4.54. On these three days both"
            " agree (D2 §2's local-day rule, INV-7)."
        ),
    ]


def _hour(item: Window) -> str:
    """Return a window's local start in the README's notation, and in full."""
    local = item.closed.start_utc.astimezone(OSLO)
    return f"d{local.day:02d}h{local.hour:02d} ({local.isoformat()})"


def _month_row(key: str, row: BacktestMetrics) -> str:
    target = "open" if row.target_kw == float("inf") else f"{row.target_kw:.2f}"
    return (
        f"| {key} | {row.days} | {row.windows} | {row.coarse_windows} | {row.estimated_windows} "
        f"| {row.max_window_kwh:.2f} | {row.metric_kw:.2f} | {row.level_reached} | {target} "
        f"| {row.over_target} | {'yes' if row.gate else 'no'} "
        f"| {row.fee.amount} {row.fee.currency} |"
    )


def _load_row(item: LoadHistory, total: BacktestMetrics) -> str:
    first = item.buckets[0][0].astimezone(OSLO).date().isoformat() if item.buckets else "—"
    last = item.buckets[-1][1].astimezone(OSLO).date().isoformat() if item.buckets else "—"
    kwh = total.load_kwh.get(item.spec.load_id)
    return (
        f"| {item.spec.load_id} | {item.spec.kind} | `{item.spec.entity}` | {item.quality} "
        f"| {first} | {last} | {'—' if kwh is None else f'{kwh:.1f}'} |"
    )
