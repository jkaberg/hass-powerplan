"""D9 §9 12 - `tools/preset_age.py` lists exactly the versions verified too long ago."""

from __future__ import annotations

from datetime import date

from tools import preset_age


def test_12_nothing_is_stale_on_the_day_it_was_read() -> None:
    """Every shipped version was read on the same day, the fixture's."""
    assert preset_age.stale(date(2026, 9, 23)) == []
    assert preset_age.stale(date(2027, 3, 23)) == [], "six months to the day is not yet stale"


def test_12_every_priced_version_is_listed_once_its_source_is_six_months_old() -> None:
    """A day past six months lists each version with a price - and no template."""
    rows = preset_age.stale(date(2027, 3, 29))
    names = {row.name for row in rows}
    assert "no/tensio-ts" in names
    assert "be/fluvius-imewo" in names
    assert not names & {"no/template", "es/2_0td", "nl/connection"}
    tensio = [row.valid_from for row in rows if row.name == "no/tensio-ts"]
    assert tensio == ["2025-07-01", "2026-01-01", "2026-07-01"]
    assert all(row.verified == date(2026, 9, 23) for row in rows)
    assert all(row.source_url.startswith("https://") for row in rows)


def test_12_the_report_warns_and_never_fails(capsys: object) -> None:
    """Exit 0 either way; the table names what to read again."""
    assert preset_age.main(["--at", "2027-06-01"]) == 0
    assert preset_age.main(["--at", "2026-09-23"]) == 0
    text = preset_age.report(preset_age.stale(date(2027, 6, 1)), date(2027, 6, 1), 6)
    assert text.splitlines()[2] == "| preset | version | verified | source |"
