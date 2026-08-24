"""The `tools/backtest.py` command line (D9 §6).

A tool someone runs by hand on a 3 GB copy of their own house has to be
unsurprising: `--help` works, a run writes the JSON it promised, an unknown
preset and an unknowable time zone are refused with a sentence that says what to
do instead. The flag parsers are unit-tested because a mistyped `--loads` should
name the item it could not read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from custom_components.powerplan.core.tariffs import AUTO, Target
from custom_components.powerplan.core.tariffs.presets.loader import load
from tests.backtest.conftest import OSLO, House, Stat, september, write_csv, write_recorder
from tools.backtest import (
    LoadSpec,
    build_parser,
    main,
    parse_loads,
    parse_target,
    read_recorder,
    render_table,
    run,
)

if TYPE_CHECKING:
    from pathlib import Path

REGISTER = "sensor.dataskap_strommaler_energy"
COPY = "copy.db"

TWO_DAYS = [1.0] * 20 + [4.0, 6.5, 2.0, 1.0] + [1.0] * 20 + [4.0, 7.5, 2.0, 1.0] + [1.0]


def _database(tmp_path: Path) -> Path:
    home = House(trace=september(TWO_DAYS))
    return write_recorder(tmp_path / COPY, (Stat(REGISTER, "kWh", home.register()),))


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`--help` prints the usage, including the CSV layout, and exits 0."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert "--recorder" in printed
    assert "grid_register.csv" in printed


def test_a_full_run_prints_a_table_and_writes_the_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The end-to-end path a maintainer runs (D9 §5.12)."""
    out = tmp_path / "run.json"
    code = main(
        [
            "--recorder",
            str(_database(tmp_path)),
            "--preset",
            "no/tensio-ts",
            "--register",
            REGISTER,
            "--out",
            str(out),
        ]
    )

    assert code == 0
    printed = capsys.readouterr().out
    assert "2026-09" in printed
    assert "5–10 kW" in printed or "2–5 kW" in printed

    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["preset"] == "no.tensio-ts.household"
    assert written["tz"] == "Europe/Oslo"
    assert written["tz_source"].endswith("no/tensio-ts.json")
    assert set(written["months"]) == {"2026-09"}
    assert written["total"]["windows"] == written["months"]["2026-09"]["windows"]
    # WP0.10 fills these; until then they are None and a note says why.
    assert written["total"]["cost_energy"] is None
    assert written["total"]["savings"] is None
    assert any("WP0.10" in note for note in written["notes"])


def test_an_unknown_preset_is_refused(tmp_path: Path) -> None:
    """A typo in `--preset` names the preset, and nothing is read."""
    with pytest.raises(SystemExit, match="no/tensio-tss"):
        main(
            [
                "--recorder",
                str(_database(tmp_path)),
                "--preset",
                "no/tensio-tss",
                "--register",
                REGISTER,
            ]
        )


def test_recorder_mode_needs_a_register(tmp_path: Path) -> None:
    """Without `--register` there is nothing to reconstruct from."""
    with pytest.raises(SystemExit, match="--register"):
        main(["--recorder", str(_database(tmp_path)), "--preset", "no/tensio-ts"])


def test_a_missing_database_is_refused(tmp_path: Path) -> None:
    """A path that is not a file is said so, not opened."""
    with pytest.raises(SystemExit, match="is not a file"):
        main(
            [
                "--recorder",
                str(tmp_path / "nope.db"),
                "--preset",
                "no/tensio-ts",
                "--register",
                REGISTER,
            ]
        )


def test_a_zone_no_source_knows_is_refused(tmp_path: Path) -> None:
    """No zone anywhere means `--tz`, never a default."""
    home = House(trace=september(TWO_DAYS))
    directory = write_csv(tmp_path / "export", register=list(home.register()))

    with pytest.raises(SystemExit, match="--tz"):
        main(["--csv", str(directory), "--preset", "custom"])


def test_the_tz_flag_wins(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--tz` overrides the preset's own zone, and the report says so."""
    code = main(
        [
            "--recorder",
            str(_database(tmp_path)),
            "--preset",
            "no/tensio-ts",
            "--register",
            REGISTER,
            "--tz",
            "Europe/Brussels",
        ]
    )

    assert code == 0
    assert "tz Europe/Brussels (from --tz)" in capsys.readouterr().out


def test_an_unknown_zone_is_refused(tmp_path: Path) -> None:
    """A zone this system does not know is refused by name."""
    with pytest.raises(SystemExit, match="Mars/Olympus"):
        main(
            [
                "--recorder",
                str(_database(tmp_path)),
                "--preset",
                "no/tensio-ts",
                "--register",
                REGISTER,
                "--tz",
                "Mars/Olympus",
            ]
        )


def test_two_sources_are_mutually_exclusive(tmp_path: Path) -> None:
    """`--recorder` and `--csv` answer the same question two ways."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--recorder", "a.db", "--csv", "b", "--preset", "no/tensio-ts"])


# --------------------------------------------------------------------------- #
# the flag parsers
# --------------------------------------------------------------------------- #


def test_loads_parse_inline() -> None:
    """`id=entity:type:nameplate`, comma-separated, with the tail optional."""
    parsed = parse_loads(
        "ev=sensor.garasje_billader_power:ev:11000,tank=switch.bereder:water_heater,x=sensor.y"
    )

    assert parsed == (
        LoadSpec("ev", "ev", "sensor.garasje_billader_power", 11000.0),
        LoadSpec("tank", "water_heater", "switch.bereder", None),
        LoadSpec("x", "", "sensor.y", None),
    )


def test_a_load_without_an_entity_is_refused() -> None:
    """A mapping the tool cannot read names the item, not the whole flag."""
    with pytest.raises(ValueError, match=r"sensor\.only"):
        parse_loads("sensor.only")


def test_loads_parse_from_json(tmp_path: Path) -> None:
    """A JSON file carries the same three fields per load."""
    path = tmp_path / "loads.json"
    path.write_text(
        '{"loads": [{"id": "ev", "type": "ev", "entity": "sensor.e", "nameplate_w": 7400}]}',
        encoding="utf-8",
    )

    assert parse_loads(str(path)) == (LoadSpec("ev", "ev", "sensor.e", 7400.0),)


def test_targets_parse() -> None:
    """`auto`, `step:<n>` and a bare number of kW."""
    assert parse_target("auto") == AUTO
    assert parse_target("step:2") == Target("step", step_index=2)
    assert parse_target("7.5") == Target("kw", kw=7.5)


def test_the_table_has_a_row_per_period_a_total_and_the_notes(tmp_path: Path) -> None:
    """The report is read by a human, so its shape is part of the deliverable."""
    result = run(
        read_recorder(
            _database(tmp_path),
            register=REGISTER,
            loads=(LoadSpec("ev", "ev", "sensor.missing", 11000.0),),
        ),
        load("no/tensio-ts"),
        OSLO,
    )
    table = render_table(result)

    lines = table.splitlines()
    assert any(line.startswith("2026-09 ") for line in lines)
    assert any(line.startswith("total ") for line in lines)
    assert "no history: ev" in table
    assert "notes" in table
    assert table.count("NOK") >= 2
