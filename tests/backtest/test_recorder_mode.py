"""`tools/backtest.py` over a recorder database (D9 §5.4, §2; PLAN §6 R8).

The tool's job in this mode is arithmetic on someone else's history: read the
long-term statistics, rebuild the windows the tariff measures, hand them to D2,
and say how good the reconstruction was. So every test here asserts one of three
things - the windows are the trace's own energy, the money is what D2 computes on
the same windows, or the quality flag tells the truth about what the database
actually held.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.pricing.holidays import NoHolidays
from custom_components.powerplan.core.tariffs import (
    AUTO,
    Evaluator,
    Target,
    seed_from_windows,
)
from tests.backtest.conftest import OSLO, House, Stat, States, flat_hours, september, write_recorder
from tests.builders.presets import fixture_preset
from tools.backtest import LoadSpec, read_recorder, run

if TYPE_CHECKING:
    from pathlib import Path

    from tests.builders.histories import Trace

REGISTER = "sensor.dataskap_strommaler_energy"
POWER = "sensor.dataskap_strommaler_power"
EV_ENERGY = "sensor.garasje_billader_energy"
FLOOR_POWER = "sensor.stua_gulvvarme_electric_consumption_w"
TANK_SWITCH = "switch.varmtvannsbereder"

#: Five September days: three quiet ones, then two evenings that lift the month
#: into Tensio's 5–10 kW step. One trace point per hour, so an hour between two
#: equal points has exactly that energy (conftest `flat_hours`).
FIVE_DAYS = (
    [1.0] * 24
    + [1.0] * 24
    + [1.0] * 21
    + [5.3, 5.3, 4.5]
    + [1.0] * 18
    + [9.2, 9.2, 1.0, 1.0, 1.0, 1.0]
    + [1.0] * 18
    + [10.6, 10.6, 1.0, 1.0, 1.0, 1.0]
    + [1.0]
)

#: The same five days with a 5.5 kWh evening that does not raise the step.
FOUR_QUIET_DAYS = (
    [0.5] * 20
    + [4.5, 4.5, 0.5, 0.5]
    + [0.5] * 20
    + [4.5, 4.5, 0.5, 0.5]
    + [0.5] * 20
    + [4.5, 4.5, 0.5, 0.5]
    + [0.5] * 20
    + [5.5, 5.5, 0.5, 0.5]
    + [0.5]
)


def house(kw: list[float] | None = None) -> House:
    """Return the five-day September house, or another hourly shape."""
    return House(trace=september(FIVE_DAYS if kw is None else kw))


def ev_trace(home: House) -> Trace:
    """Build a charger that draws 7 kW for two hours in four, over the same span."""
    return flat_hours(home.trace.start, [0.0] * 20 + [7.0, 7.0, 0.0, 0.0] * 25 + [0.0])


def recorder(
    tmp_path: Path,
    home: House,
    *,
    register: bool = True,
    power: bool = False,
    loads: tuple[Stat, ...] = (),
    states: tuple[States, ...] = (),
    register_rows: list[tuple[datetime, float]] | None = None,
) -> Path:
    """Write the house into a recorder database and return its path."""
    stats: list[Stat] = []
    if register:
        rows = home.register() if register_rows is None else register_rows
        stats.append(Stat(REGISTER, "kWh", rows, kind="sum"))
    if power:
        stats.append(Stat(POWER, "W", home.means(5), kind="mean", short_term=True))
    stats.extend(loads)
    return write_recorder(tmp_path / "recorder.db", stats, states)


# --------------------------------------------------------------------------- #
# the windows
# --------------------------------------------------------------------------- #


def test_register_windows_are_the_traces_own_energy(tmp_path: Path) -> None:
    """Hourly LTS sums rebuild exactly the windows the trace holds (D3 §5.11)."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)
    result = run(history, fixture_preset("no/tensio-ts"), OSLO)

    expected = home.window_kwh(60)
    got = {window.closed.start_utc: window.closed.kwh for window in result.windows}
    assert got.keys() == expected.keys()
    for start, kwh in expected.items():
        assert got[start] == pytest.approx(kwh, abs=1e-9)
    assert {window.origin for window in result.windows} == {"register"}
    assert {window.closed.confidence for window in result.windows} == {"exact"}


def test_monthly_metric_level_and_fee_agree_with_d2_on_the_same_windows(tmp_path: Path) -> None:
    """The tool reports D2's numbers, not its own (D2 §3 `bill`)."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)
    result = run(history, fixture_preset("no/tensio-ts"), OSLO)

    reference = Evaluator(fixture_preset("no/tensio-ts"), tz=OSLO, calendar=NoHolidays())
    seed_from_windows(reference, [window.closed for window in result.windows])
    bill = reference.bill(reference.period(result.windows[-1].closed.start_utc))

    month = result.months["2026-09"]
    assert month.metric_kw == bill.metric_kw
    assert month.level_reached == bill.level.name
    assert month.fee == bill.capacity_fee
    assert month.confidence == bill.level.confidence
    # (10.6 + 9.2 + 5.3) / 3 = 8.37 kW: the third step of Tensio TS's table (397 from 2026-07-01).
    assert month.metric_kw == pytest.approx((10.6 + 9.2 + 5.3) / 3)
    assert month.level_reached == "5–10 kW"
    assert str(month.fee.amount) == "397"
    assert month.windows == len(result.windows)


def test_max_window_and_days_describe_what_was_read(tmp_path: Path) -> None:
    """`max_window_kwh` and `days` come from the windows, not from the tariff."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)
    result = run(history, fixture_preset("no/tensio-ts"), OSLO)

    month = result.months["2026-09"]
    assert month.max_window_kwh == pytest.approx(max(home.window_kwh(60).values()))
    assert month.days == len(
        {window.closed.start_utc.astimezone(OSLO).date() for window in result.windows}
    )


# --------------------------------------------------------------------------- #
# quality: coarse, estimated, gaps
# --------------------------------------------------------------------------- #


def test_hourly_sums_are_coarse_for_a_quarter_hour_tariff(tmp_path: Path) -> None:
    """Hourly LTS can never yield 15-min windows; D2 marks them coarse (R8)."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)
    result = run(history, fixture_preset("be/fluvius-imewo"), OSLO)

    month = result.months["2026-09"]
    # Every hour was split four ways and every split is coarse (D2 §5.1).
    assert month.windows == 4 * len(result.windows)
    assert month.coarse_windows == month.windows
    assert any("hourly" in note for note in result.notes)
    # D2's own flag, not the tool's counter: the evaluator marks each split part.
    reference = Evaluator(fixture_preset("be/fluvius-imewo"), tz=OSLO, calendar=NoHolidays())
    seed_from_windows(reference, [window.closed for window in result.windows])
    assert all(record.coarse for record in reference.history.windows.values())
    # Fluvius bills a rolling twelve months and one is known, so D2 says
    # `partial` before it says `coarse` (D2 §5.3).
    assert month.confidence == "partial"


def test_power_means_fill_a_register_gap(tmp_path: Path) -> None:
    """Where the register has no row, the power mean does - marked estimated."""
    home = house()
    rows = home.register()
    path = recorder(
        tmp_path,
        home,
        power=True,
        register_rows=rows[:30] + rows[33:],
    )

    result = run(
        read_recorder(path, register=REGISTER, power=POWER), fixture_preset("no/tensio-ts"), OSLO
    )

    from_power = [window for window in result.windows if window.origin == "power"]
    expected = home.window_kwh(60)
    assert len(from_power) == 4
    assert {window.closed.confidence for window in from_power} == {"estimated"}
    for window in from_power:
        assert window.closed.kwh == pytest.approx(expected[window.closed.start_utc], rel=1e-9)
    assert result.months["2026-09"].estimated_windows == 4
    assert result.months["2026-09"].windows == len(expected)


def test_a_gap_no_source_covers_is_reported_not_filled(tmp_path: Path) -> None:
    """A hole is honest: the window count drops and a note says by how much."""
    home = house()
    rows = home.register()
    path = recorder(tmp_path, home, register_rows=rows[:30] + rows[33:])

    result = run(read_recorder(path, register=REGISTER), fixture_preset("no/tensio-ts"), OSLO)

    assert result.months["2026-09"].windows == len(home.window_kwh(60)) - 4
    assert any("4 window" in note for note in result.notes)


def test_no_register_and_no_power_is_an_empty_run(tmp_path: Path) -> None:
    """Nothing to read is reported, never inferred."""
    path = write_recorder(tmp_path / "empty.db", ())
    result = run(read_recorder(path, register=REGISTER), fixture_preset("no/tensio-ts"), OSLO)

    assert result.windows == ()
    assert result.months == {}
    assert result.total.windows == 0
    assert result.total.reconstruction == "none"
    assert any("no rows" in note for note in result.notes)


# --------------------------------------------------------------------------- #
# per-load reconstruction
# --------------------------------------------------------------------------- #


def test_reconstruction_is_full_when_every_load_has_a_history(tmp_path: Path) -> None:
    """Energy and power histories are both first-class (D9 §2)."""
    home = house()
    charger = ev_trace(home)
    loads = (
        Stat(EV_ENERGY, "kWh", home.register(charger), kind="sum"),
        Stat(FLOOR_POWER, "W", home.means(60, charger), kind="mean"),
    )
    history = read_recorder(
        recorder(tmp_path, home, loads=loads),
        register=REGISTER,
        loads=(
            LoadSpec("ev", "ev", EV_ENERGY, nameplate_w=11000.0),
            LoadSpec("floor_stua", "floor_heating", FLOOR_POWER, nameplate_w=600.0),
        ),
    )
    month = run(history, fixture_preset("no/tensio-ts"), OSLO).months["2026-09"]

    assert month.reconstruction == "full"
    assert month.load_quality == {"ev": "energy", "floor_stua": "power"}
    assert month.load_kwh["ev"] > 0.0
    assert month.load_kwh["floor_stua"] == pytest.approx(month.load_kwh["ev"], rel=1e-6)


def test_a_switch_only_load_is_nameplate_times_on_fraction_and_partial(tmp_path: Path) -> None:
    """`nameplate × on_fraction` where only on/off exists (D9 §2, R8)."""
    home = house()
    day = datetime(2026, 9, 2, 0, 0, tzinfo=OSLO).astimezone(UTC)
    states = (
        States(
            TANK_SWITCH,
            [
                (day, "off"),
                (day + timedelta(hours=2), "on"),
                (day + timedelta(hours=3), "off"),
            ],
        ),
    )
    loads = (Stat(EV_ENERGY, "kWh", home.register(ev_trace(home)), kind="sum"),)
    history = read_recorder(
        recorder(tmp_path, home, loads=loads, states=states),
        register=REGISTER,
        loads=(
            LoadSpec("ev", "ev", EV_ENERGY, nameplate_w=11000.0),
            LoadSpec("tank", "water_heater", TANK_SWITCH, nameplate_w=2000.0),
        ),
    )
    month = run(history, fixture_preset("no/tensio-ts"), OSLO).months["2026-09"]

    assert month.reconstruction == "partial"
    assert month.load_quality["tank"] == "on_fraction"
    assert month.load_kwh["tank"] == pytest.approx(2.0)  # 2 kW for one hour


def test_a_load_with_no_history_at_all_is_missing(tmp_path: Path) -> None:
    """A mapped load the database never heard of reads `missing`, not zero."""
    home = house()
    history = read_recorder(
        recorder(tmp_path, home),
        register=REGISTER,
        loads=(LoadSpec("ev", "ev", EV_ENERGY, nameplate_w=11000.0),),
    )
    month = run(history, fixture_preset("no/tensio-ts"), OSLO).months["2026-09"]

    assert month.reconstruction == "none"
    assert month.load_quality == {"ev": "missing"}
    assert "ev" not in month.load_kwh


def test_no_loads_mapped_is_reconstruction_none(tmp_path: Path) -> None:
    """The site's own windows are still billed; the loads are simply unknown."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)
    month = run(history, fixture_preset("no/tensio-ts"), OSLO).months["2026-09"]

    assert month.reconstruction == "none"
    assert month.level_reached == "5–10 kW"


# --------------------------------------------------------------------------- #
# the target
# --------------------------------------------------------------------------- #


def test_over_target_counts_the_windows_that_would_have_raised_the_step(
    tmp_path: Path,
) -> None:
    """`auto`: the target is the step the month itself reached (D2 §5.5)."""
    home = house(FOUR_QUIET_DAYS)
    result = run(
        read_recorder(recorder(tmp_path, home), register=REGISTER),
        fixture_preset("no/tensio-ts"),
        OSLO,
    )

    month = result.months["2026-09"]
    assert month.metric_kw == pytest.approx((5.5 + 4.5 + 4.5) / 3)
    assert month.level_reached == "2–5 kW"
    assert month.target_kw == 5.0
    assert month.over_target == 1
    assert month.gate is False


def test_every_window_under_target_is_the_gate_statement(tmp_path: Path) -> None:
    """A month whose windows all sit under the step it reached passes (PLAN §3)."""
    home = house([1.0] * 49)
    result = run(
        read_recorder(recorder(tmp_path, home), register=REGISTER),
        fixture_preset("no/tensio-ts"),
        OSLO,
    )

    month = result.months["2026-09"]
    assert month.target_kw == 2.0
    assert month.over_target == 0
    assert month.gate is True


def test_an_explicit_target_step_overrides_the_step_reached(tmp_path: Path) -> None:
    """`--target step:0` asks a different question of the same history."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)

    auto = run(history, fixture_preset("no/tensio-ts"), OSLO, target=AUTO).months["2026-09"]
    step0 = run(
        history, fixture_preset("no/tensio-ts"), OSLO, target=Target("step", step_index=0)
    ).months["2026-09"]

    assert auto.target_kw == 10.0
    assert step0.target_kw == 2.0
    assert step0.over_target > auto.over_target


def test_an_open_ended_step_can_never_be_exceeded(tmp_path: Path) -> None:
    """The top step's ceiling is `inf`, so `over_target` is 0 by construction."""
    home = house()
    history = read_recorder(recorder(tmp_path, home), register=REGISTER)
    month = run(
        history, fixture_preset("no/tensio-ts"), OSLO, target=Target("step", step_index=14)
    ).months["2026-09"]

    assert month.target_kw == float("inf")
    assert month.over_target == 0


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #


def test_the_autumn_dst_day_has_twenty_five_windows(tmp_path: Path) -> None:
    """A DST day's window count is a property of the day, not of the clock."""
    start = datetime(2026, 10, 24, 0, 0, tzinfo=OSLO).astimezone(UTC)
    home = House(trace=flat_hours(start, [1.0] * 74))
    result = run(
        read_recorder(recorder(tmp_path, home), register=REGISTER),
        fixture_preset("no/tensio-ts"),
        OSLO,
    )

    per_day: dict[str, int] = {}
    for window in result.windows:
        key = window.closed.start_utc.astimezone(OSLO).date().isoformat()
        per_day[key] = per_day.get(key, 0) + 1
    assert per_day["2026-10-24"] == 24
    assert per_day["2026-10-25"] == 25
    assert per_day["2026-10-26"] == 24


def test_months_are_split_on_the_local_calendar(tmp_path: Path) -> None:
    """A window at 22:00 UTC on the 31st belongs to the next local month."""
    start = datetime(2026, 8, 31, 20, 0, tzinfo=OSLO).astimezone(UTC)
    home = House(trace=flat_hours(start, [2.0] * 10))
    result = run(
        read_recorder(recorder(tmp_path, home), register=REGISTER),
        fixture_preset("no/tensio-ts"),
        OSLO,
    )

    assert set(result.months) == {"2026-08", "2026-09"}
    assert result.months["2026-08"].windows == 4
    assert result.months["2026-09"].windows == 5
    assert result.total.windows == sum(month.windows for month in result.months.values())


def test_from_and_to_clip_the_span(tmp_path: Path) -> None:
    """`--from/--to` are local instants and they clip what is read."""
    home = house()
    history = read_recorder(
        recorder(tmp_path, home),
        register=REGISTER,
        start=datetime(2026, 9, 3, 0, 0, tzinfo=OSLO),
        end=datetime(2026, 9, 4, 0, 0, tzinfo=OSLO),
    )
    result = run(history, fixture_preset("no/tensio-ts"), OSLO)

    assert result.months["2026-09"].windows == 24
    assert result.total.max_window_kwh == pytest.approx(5.3)
