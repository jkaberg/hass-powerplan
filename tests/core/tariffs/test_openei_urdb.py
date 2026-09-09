"""The US's rates from URDB (D13 §19 8; D1 §9 7; §5.5, §5.11).

APS's captured R-3 of 2026: summer and winter demand as dated versions, weekday
16–19 eligible, the window asked with 60 pre-selected, a fee per day. The 12 × 24
energy importer reads every cell as the document says and round-trips.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs.household import EXCL, EnergyVersion
from custom_components.powerplan.core.tariffs.model import Linear, PeakTariff, TimeFilter
from custom_components.powerplan.core.tariffs.sources import openei_urdb
from tests.builders.tariff_sources import CAPTURED, FIXTURES
from tests.core.tariffs.conftest import Holidays

PHOENIX = ZoneInfo("America/Phoenix")
APS = FIXTURES / "openei" / "aps-r3-69a718961822c9da260daf1b.json"


def _parse(**answers: Any):  # type: ignore[no-untyped-def]
    return openei_urdb.parse(APS.read_bytes(), "803", fetched=CAPTURED, answers=answers)


def test_8_aps_r3_is_a_version_per_season() -> None:
    """Summer $19.585 + $1.04, winter $13.747 + $1.04 per kW; weekdays 16–19."""
    grid = _parse().grid
    assert [v.valid_from for v in grid.capacity] == [
        date(2026, 9, 1),
        date(2026, 11, 1),
        date(2027, 5, 1),
    ]
    prices = []
    for version in grid.capacity:
        [peak] = version.peaks
        assert isinstance(peak.pricing, Linear)
        prices.append(peak.pricing.price_per_kw.amount)
        assert peak.eligible == TimeFilter(weekdays=(0, 1, 2, 3, 4), hours=((960, 1140),))
        assert (peak.per_day, peak.per_period, peak.period) == ("max", "max", "month")
    assert prices == [Decimal("20.625"), Decimal("14.787"), Decimal("20.625")]
    assert grid.basis == EXCL
    assert (grid.fixed_fee[0].amount, grid.fixed_fee[0].per) == (Decimal("0.458"), "day")


def test_8_the_window_is_asked_with_60_pre_selected() -> None:
    """URDB gives no `demandwindow`: asked; the answer builds the copy."""
    [question] = _parse().questions
    assert (question.key, question.default) == ("window_min", "60")
    answered = _parse(window_min="15")
    assert not answered.questions
    assert all(p.window_min == 15 for v in answered.grid.capacity for p in v.peaks)


def test_the_zip_codes_utilities_are_the_operators() -> None:
    """85004 is APS; its rates in force are the products, R-3 among them."""
    [aps] = openei_urdb.operators((FIXTURES / "openei" / "rates-85004.json").read_bytes(), CAPTURED)
    assert (aps.key, aps.name) == ("803", "Arizona Public Service Co")
    assert "69a718961822c9da260daf1b" in [product.key for product in aps.products]


def test_a_block_demand_rate_is_refused() -> None:
    """Two tiers on a demand period: not approximated (rule 9)."""
    rate = json.loads(APS.read_bytes())
    rate["items"][0]["demandratestructure"][2].append({"rate": 30, "max": 10})
    with pytest.raises(openei_urdb.UrdbError, match="block demand"):
        openei_urdb.parse(json.dumps(rate).encode(), "803", fetched=CAPTURED, answers={})


# --------------------------------------------------------------------------- #
# D1 §9 7 - the 12 × 24 energy importer (moved from `tou_urdb`)
# --------------------------------------------------------------------------- #

SUMMER = (5, 6, 7, 8, 9, 10)
OFF_PEAK, MID_PEAK, ON_PEAK = 0, 1, 2
RATES = (Decimal("0.0765"), Decimal("0.1122"), Decimal("0.2480"))


def urdb_doc() -> dict[str, Any]:
    """Return an APS-shaped TOU rate: summer on-peak 15–20 on weekdays; rate + adj per period."""
    weekday = [
        [
            ON_PEAK
            if month in SUMMER and 15 <= hour < 20
            else MID_PEAK
            if 7 <= hour < 22
            else OFF_PEAK
            for hour in range(24)
        ]
        for month in range(1, 13)
    ]
    return {
        "energyratestructure": [
            [{"rate": 0.0745, "adj": 0.002, "unit": "kWh"}],
            [{"rate": 0.1102, "adj": 0.002, "unit": "kWh"}],
            [{"rate": 0.246, "adj": 0.002, "unit": "kWh"}],
        ],
        "energyweekdayschedule": weekday,
        "energyweekendschedule": [[OFF_PEAK] * 24 for _ in range(12)],
    }


def _first(year: int, month: int, weekdays: tuple[int, ...]) -> date:
    day = date(year, month, 1)
    while day.weekday() not in weekdays:
        day += timedelta(days=1)
    return day


def _price(version: EnergyVersion, when: datetime) -> Decimal:
    return next(
        (
            p.price
            for p in version.periods
            if p.when is None or p.when.matches(when, PHOENIX, Holidays())
        ),
        version.fallback,
    )


def test_07_every_cell_is_priced_as_the_document_says() -> None:
    """Every (month, hour, day kind) maps to its own rate."""
    doc = urdb_doc()
    version = EnergyVersion(valid_from=date(2027, 1, 1), periods=openei_urdb.energy_periods(doc))
    for month in range(1, 13):
        for hour in range(24):
            for key, days in (
                ("energyweekdayschedule", (0, 1, 2, 3, 4)),
                ("energyweekendschedule", (5, 6)),
            ):
                when = datetime.combine(_first(2027, month, days), time(hour), tzinfo=PHOENIX)
                assert _price(version, when) == RATES[doc[key][month - 1][hour]], (month, hour, key)


def test_07_the_matrices_round_trip() -> None:
    """Exporting the imported periods gives the matrices and rates back, twice around."""
    doc = urdb_doc()
    version = EnergyVersion(valid_from=date(2027, 1, 1), periods=openei_urdb.energy_periods(doc))
    exported = openei_urdb.matrices(version, 2027, PHOENIX, Holidays())
    assert exported["energyweekdayschedule"] == doc["energyweekdayschedule"]
    assert exported["energyweekendschedule"] == doc["energyweekendschedule"]
    assert exported["energyratestructure"] == [[{"rate": rate}] for rate in RATES]
    again = EnergyVersion(valid_from=date(2027, 1, 1), periods=openei_urdb.energy_periods(exported))
    assert openei_urdb.matrices(again, 2027, PHOENIX, Holidays()) == exported


def test_07_a_malformed_document_is_refused_by_name() -> None:
    """Each way the matrices can be wrong says which."""
    doc = urdb_doc()
    with pytest.raises(openei_urdb.UrdbError, match="energyratestructure"):
        openei_urdb.energy_periods({k: v for k, v in doc.items() if k != "energyratestructure"})
    with pytest.raises(openei_urdb.UrdbError, match="12 rows"):
        openei_urdb.energy_periods(
            {**doc, "energyweekdayschedule": doc["energyweekdayschedule"][:11]}
        )
    with pytest.raises(openei_urdb.UrdbError, match="23 hours, not 24"):
        openei_urdb.energy_periods({**doc, "energyweekendschedule": [[0] * 23 for _ in range(12)]})
    with pytest.raises(openei_urdb.UrdbError, match="names period 9"):
        openei_urdb.energy_periods({**doc, "energyweekdayschedule": [[9] * 24 for _ in range(12)]})


def test_every_peak_is_a_peak_tariff() -> None:
    """The copy builds into the tariff model (D13 §19 4)."""
    assert all(isinstance(rule, PeakTariff) for v in _parse().grid.capacity for rule in v.rules)
