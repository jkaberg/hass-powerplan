"""`tools/backtest.py` over a CSV export (D9 §2 "Backtest for non-Norwegian houses").

The CSV path exists for a house whose recorder is gone, or was never Norwegian,
or lives in an installation nobody wants to copy a 3 GB database out of. It has
to be the same backtest: the first test writes one synthetic history into both a
recorder database and a CSV directory and asserts the two runs agree row for row.
The rest cover what only a text export can get wrong - a naive timestamp, a
missing file, a load whose only record is a switch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.backtest.conftest import OSLO, House, Stat, september, write_csv, write_recorder
from tests.builders.presets import fixture_preset
from tools.backtest import LoadSpec, read_csv, read_recorder, resolve_tz, run

if TYPE_CHECKING:
    from pathlib import Path

REGISTER = "sensor.dataskap_strommaler_energy"

THREE_DAYS = [1.0] * 20 + [4.0, 6.5, 2.0, 1.0] + [1.0] * 20 + [4.0, 7.5, 2.0, 1.0] + [1.0] * 25


def house() -> House:
    """Return a three-day September house with two evening peaks."""
    return House(trace=september(THREE_DAYS))


def _instants(home: House) -> list[tuple[datetime, float]]:
    """Return the register as a CSV holds it: the value AT the timestamp."""
    return [
        (at + timedelta(hours=1), value)
        for at, value in home.register()
        if at >= home.trace.start - timedelta(hours=1)
    ]


def test_csv_and_recorder_agree_on_the_same_history(tmp_path: Path) -> None:
    """One history, two readers, the same bill (D9 §5.4)."""
    home = house()
    database = write_recorder(tmp_path / "r.db", (Stat(REGISTER, "kWh", home.register()),))
    directory = write_csv(tmp_path / "export", register=_instants(home))

    from_db = run(read_recorder(database, register=REGISTER), fixture_preset("no/tensio-ts"), OSLO)
    from_csv = run(read_csv(directory), fixture_preset("no/tensio-ts"), OSLO)

    assert [window.closed for window in from_csv.windows] == [
        window.closed for window in from_db.windows
    ]
    assert from_csv.months["2026-09"].as_dict() == from_db.months["2026-09"].as_dict()
    assert from_csv.total.fee == from_db.total.fee


def test_meta_json_supplies_the_zone(tmp_path: Path) -> None:
    """An export carries its own zone; the tool says where the zone came from."""
    home = house()
    directory = write_csv(
        tmp_path / "export", register=_instants(home), meta={"tz": "Europe/Brussels"}
    )
    history = read_csv(directory)
    zone, source = resolve_tz(None, history, None, "tensio-ts.json")

    assert history.tz_name == "Europe/Brussels"
    assert str(zone) == "Europe/Brussels"
    assert "csv" in source


def test_meta_json_window_min_overrides_the_preset(tmp_path: Path) -> None:
    """A 15-minute export is read at 15 minutes even under an hourly preset."""
    home = house()
    directory = write_csv(tmp_path / "export", register=_instants(home), meta={"window_min": 15})
    result = run(read_csv(directory), fixture_preset("no/tensio-ts"), OSLO)

    assert result.window_min == 15
    # The rows are still hourly, so the reconstruction stays hourly and says so.
    assert any("hourly" in note or "cannot make" in note for note in result.notes)


def test_a_timestamp_with_no_offset_is_refused(tmp_path: Path) -> None:
    """A naive timestamp is a guessed zone; the tool refuses it."""
    directory = tmp_path / "export"
    directory.mkdir()
    (directory / "grid_register.csv").write_text(
        "timestamp,kwh\n2026-09-01T00:00:00,100.0\n2026-09-01T01:00:00,101.0\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="never guesses a zone"):
        read_csv(directory)


def test_a_missing_register_file_is_a_note_not_a_crash(tmp_path: Path) -> None:
    """Nothing to read is reported (PLAN §6 R8)."""
    directory = tmp_path / "export"
    directory.mkdir()
    result = run(read_csv(directory), fixture_preset("no/tensio-ts"), OSLO)

    assert result.windows == ()
    assert any("grid_register.csv" in note for note in result.notes)


def test_power_alone_reconstructs_every_window_as_estimated(tmp_path: Path) -> None:
    """With no register at all the power export carries the whole span."""
    home = house()
    directory = write_csv(
        tmp_path / "export",
        power=[(at, watts) for at, _, watts in _power_rows(home)],
    )
    result = run(read_csv(directory), fixture_preset("no/tensio-ts"), OSLO)

    expected = home.window_kwh(60)
    assert {window.origin for window in result.windows} == {"power"}
    assert len(result.windows) == len(expected)
    for window in result.windows:
        assert window.closed.kwh == pytest.approx(expected[window.closed.start_utc], rel=1e-9)
        assert window.closed.confidence == "estimated"
    assert result.months["2026-09"].estimated_windows == len(result.windows)


def _power_rows(home: House) -> list[tuple[datetime, float, float]]:
    return [(at, 3600.0, watts) for at, watts in home.means(60)]


def test_load_csvs_are_read_by_their_role(tmp_path: Path) -> None:
    """`.energy`, `.power` and `.onoff` are three ways to describe one load."""
    home = house()
    charger = september([0.0] * 20 + [7.0, 7.0, 0.0, 0.0] * 12 + [0.0])
    day = datetime(2026, 9, 2, 0, 0, tzinfo=OSLO).astimezone(UTC)
    directory = write_csv(
        tmp_path / "export",
        register=_instants(home),
        load_energy={"ev": [(at + timedelta(hours=1), kwh) for at, kwh in home.register(charger)]},
        load_power={"floor": [(at, watts) for at, watts in home.means(60, charger)]},
        load_onoff={
            "tank": [(day, 0), (day + timedelta(hours=2), 1), (day + timedelta(hours=3), 0)]
        },
    )
    history = read_csv(
        directory,
        loads=(
            LoadSpec("ev", "ev", "ev", nameplate_w=11000.0),
            LoadSpec("floor", "floor_heating", "floor", nameplate_w=600.0),
            LoadSpec("tank", "water_heater", "tank", nameplate_w=2000.0),
        ),
    )
    month = run(history, fixture_preset("no/tensio-ts"), OSLO).months["2026-09"]

    assert month.load_quality == {"ev": "energy", "floor": "power", "tank": "on_fraction"}
    assert month.reconstruction == "partial"
    assert month.load_kwh["tank"] == pytest.approx(2.0)
    assert month.load_kwh["ev"] == pytest.approx(month.load_kwh["floor"], rel=1e-6)


def test_a_load_with_no_csv_is_missing(tmp_path: Path) -> None:
    """A named load with no file reads `missing`, and the note names the directory."""
    home = house()
    directory = write_csv(tmp_path / "export", register=_instants(home))
    history = read_csv(directory, loads=(LoadSpec("ev", "ev", "ev", nameplate_w=11000.0),))
    result = run(history, fixture_preset("no/tensio-ts"), OSLO)

    assert result.months["2026-09"].load_quality == {"ev": "missing"}
    assert result.months["2026-09"].reconstruction == "none"
    assert any("has no CSV" in note for note in result.notes)
