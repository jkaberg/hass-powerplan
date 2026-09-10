"""D9 §9 12 - `tools/preset_age.py` lists exactly the versions verified too long ago."""

from __future__ import annotations

from datetime import date

from tools import preset_age


def test_12_nothing_is_stale_on_the_day_it_was_read() -> None:
    """Every shipped version was read on the same day, the fixture's."""
    assert preset_age.stale(date(2026, 9, 23)) == []
    assert preset_age.stale(date(2027, 3, 23)) == [], "six months to the day is not yet stale"


def test_12_only_national_rules_age_no_company_price() -> None:
    """TS.6 (INV-70): a national rule's dated fact ages (GB's no-capacity claim); no company file."""
    rows = [
        row for row in preset_age.stale(date(2027, 3, 29)) if not row.name.startswith("countries/")
    ]
    assert {row.name for row in rows} == {"uk/nopeak"}
    assert all(row.source_url.startswith("https://") for row in rows)


def test_12_the_report_warns_and_never_fails(capsys: object) -> None:
    """Exit 0 either way; the table names what to read again."""
    assert preset_age.main(["--at", "2027-06-01"]) == 0
    assert preset_age.main(["--at", "2026-09-23"]) == 0
    text = preset_age.report(preset_age.stale(date(2027, 6, 1)), date(2027, 6, 1), 6)
    assert text.splitlines()[2] == "| preset | version | verified | source |"


def test_the_country_modules_rates_are_aged_too() -> None:
    """VAT and levies are national law in code: read again after six months (D13 §12.2)."""
    assert not [
        row for row in preset_age.stale(date(2027, 3, 24)) if row.name.startswith("countries/")
    ]
    rows = [row for row in preset_age.stale(date(2027, 3, 29)) if row.name.startswith("countries/")]
    assert {row.name for row in rows} >= {"countries/no", "countries/gb", "countries/cy"}
    assert all(row.verified == date(2026, 9, 24) for row in rows)


def test_a_rate_whose_announced_end_nears_is_listed() -> None:
    """Ireland's 9 % ends 2030-12-31 with nothing written after it."""
    rows = preset_age.stale(date(2030, 8, 1))
    ireland = [row for row in rows if row.name == "countries/ie"]
    assert [row.note for row in ireland] == ["ends 2030-12-31"]
