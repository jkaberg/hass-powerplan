"""Wallonia and Brussels from the regulators' comparators (D13 §5.9; T1a, D-0575).

The captured answers for Namur (ORES) and Brussels: the grid's
lines excl. VAT, a rate as the year's amount over the kWh simulated, the dual
rate's day hours asked, BruSim's connection segment from the site's power.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.model import HolidayMode, TimeFilter
from custom_components.powerplan.core.tariffs.sources import QualityError, cwape
from tests.builders.tariff_sources import CAPTURED, FIXTURES

FOLDER = FIXTURES / "cwape"


def _parse(name: str, product: str, **answers: object):  # type: ignore[no-untyped-def]
    simulation = (FOLDER / f"{name}.json").read_bytes()
    return cwape.parse(
        simulation,
        operator="compacwape:993",
        product=product,
        company="ORES",
        fetched=CAPTURED,
        url="https://api.compacwape.be/offer_simulations",
        answers=answers,
    )


def test_a_single_rate_meter_is_one_rate_and_a_yearly_fee() -> None:
    """ORES, Namur: the grid's lines over 3 500 kWh, excl. VAT; the fixed term per year."""
    fetched = _parse("compacwape-sim-993-single", "single")
    grid = fetched.grid
    assert grid.basis == EXCL
    assert grid.energy[0].fallback == Decimal("0.147232")
    assert (grid.fixed_fee[0].amount, grid.fixed_fee[0].per) == (Decimal("14.098"), "year")
    assert not fetched.questions


def test_a_dual_rate_asks_its_day_hours() -> None:
    """Day and night from counters 3 and 4; the day's hours asked, 07–22 weekdays pre-selected."""
    fetched = _parse("compacwape-sim-993-dual", "dual")
    assert [(q.key, q.default) for q in fetched.questions] == [("rate_1_hours", "07-22")]
    answered = _parse("compacwape-sim-993-dual", "dual", rate_1_hours="06-21")
    [day] = answered.grid.energy[0].periods
    assert day.when == TimeFilter(
        weekdays=(0, 1, 2, 3, 4), hours=((360, 1260),), holidays=HolidayMode.EXCLUDE
    )
    assert day.price > answered.grid.energy[0].fallback, "the day costs more than the night"


def test_the_postcodes_entries_name_their_grid_company() -> None:
    """Namur's two entries are both ORES: one operator."""
    names = cwape.dnm_names((FOLDER / "compacwape-dnms.json").read_bytes())
    entries = cwape.postal_codes((FOLDER / "compacwape-postal-5000.json").read_bytes())
    named = [
        (
            pid,
            place,
            names[cwape.dnm_of((FOLDER / f"compacwape-sim-{pid}-single.json").read_bytes())],
        )
        for pid, place in entries
    ]
    [ores] = cwape.operators("compacwape", named)
    assert ores.name == "ORES"
    assert [p.key for p in ores.products] == ["single", "dual"]


def test_brusim_prices_the_connections_power() -> None:
    """A 9.2 kVA site is segment 3 (6.1–9.6); none given is the default, and asked."""
    segments = (FOLDER / "brusim-segments.json").read_bytes()
    assert cwape.segment_for(segments, 9.2) == ("/connection_power_segments/3", False)
    assert cwape.segment_for(segments, 12.0) == ("/connection_power_segments/4", False)
    assert cwape.segment_for(segments, None) == ("/connection_power_segments/3", True)


def test_an_answer_without_grid_lines_is_refused() -> None:
    """No `dnm` lines: not a copy (rule 9)."""
    with pytest.raises(QualityError):
        cwape.parse(
            b'{"electricity": {"dnm": [], "common": []}}',
            operator="x:1",
            product="single",
            company="x",
            fetched=CAPTURED,
            url="x",
            answers={},
        )
