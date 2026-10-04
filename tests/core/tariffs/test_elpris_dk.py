"""Denmark's grid tariffs: elpris.dk and Energinet's Datahub (D13 §19 3; §5.9, §5.11).

The captured documents: the directory by postcode, an area's C
tariff hour by hour with Energinet's per-kWh tariffs added, the subscriptions,
and Radius's winter table from Datahub - registered before elpris.dk shows it.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.tariffs.household import EXCL
from custom_components.powerplan.core.tariffs.model import NoPeak
from custom_components.powerplan.core.tariffs.sources import (
    QualityError,
    datahub_pricelist,
    elpris_dk,
)
from tests.builders.tariff_sources import CAPTURED, FIXTURES
from tests.core.tariffs.conftest import Holidays

COPENHAGEN = ZoneInfo("Europe/Copenhagen")
#: Energinet's system (0.072) and transmission (0.043) tariffs, DKK/kWh excl. VAT.
ENERGINET = Decimal("0.115")


def _json(*path: str) -> Any:
    return json.loads(FIXTURES.joinpath(*path).read_bytes())


def _radius():  # type: ignore[no-untyped-def]
    return elpris_dk.parse(
        _json("elpris_dk", "distributionAreaCharge_791.json"),
        _json("elpris_dk", "nationalCharges.json"),
        _json("datahub", "radius-DT_C_01.json")["records"],
        area="791",
        name="Radius Elnet A/S",
        fetched=CAPTURED,
    ).grid


def _price_at(version, when: datetime) -> Decimal:  # type: ignore[no-untyped-def]
    return next(
        (
            period.price
            for period in version.periods
            if period.when is None or period.when.matches(when, COPENHAGEN, Holidays())
        ),
        version.fallback,
    )


def test_the_directory_maps_a_postcode_to_its_grid_areas() -> None:
    """1812 Frederiksberg is Radius; a postcode on a boundary lists both areas."""
    static = _json("elpris_dk", "static.json")
    assert elpris_dk.areas_for(static, "1812") == ["791"]
    assert len(elpris_dk.areas_for(static, "8732")) == 2
    operators = elpris_dk.operators(static)
    assert len(operators) == 34
    assert elpris_dk.owner(static, "791") == "Radius Elnet A/S"


def test_3_the_summer_table_is_the_areas_hours_plus_energinets_tariffs() -> None:
    """Radius from 2026-04-01: 17–21 peak, 00–06 low, excl. VAT, NoPeak, monthly fees."""
    grid = _radius()
    summer = grid.energy_at(date(2026, 9, 24))
    assert summer is not None
    assert summer.valid_from == date(2026, 4, 1)
    assert (
        _price_at(summer, datetime(2026, 9, 24, 18, tzinfo=COPENHAGEN))
        == Decimal("0.4141") + ENERGINET
    )
    assert (
        _price_at(summer, datetime(2026, 9, 24, 3, tzinfo=COPENHAGEN))
        == Decimal("0.1062") + ENERGINET
    )
    assert grid.basis == EXCL
    assert all(isinstance(version.rules[0], NoPeak) for version in grid.capacity)
    assert grid.fixed_fee[0].per == "month"
    assert grid.fixed_fee[0].amount == Decimal("40.8351") + Decimal("15.5833")


def test_3_radiuss_winter_table_comes_from_datahub_before_elpris_shows_it() -> None:
    """From 2026-10-01 the evening peak is 0.955573 DKK/kWh - registered in Datahub."""
    grid = _radius()
    winter = grid.energy_at(date(2026, 10, 1))
    assert winter is not None
    assert winter.valid_from == date(2026, 10, 1)
    assert (
        _price_at(winter, datetime(2026, 11, 2, 18, tzinfo=COPENHAGEN))
        == Decimal("0.955573") + ENERGINET
    )
    assert grid.valid_to == date(2027, 3, 22)


def test_3_datahub_folds_24_hourly_prices_into_periods() -> None:
    """One version per `ValidFrom`; the commonest price is the fallback."""
    records = _json("datahub", "radius-DT_C_01.json")["records"]
    [(version, until)] = datahub_pricelist.versions(records, "DT_C_01")
    assert (version.valid_from, until) == (date(2026, 10, 1), date(2027, 3, 23))
    assert version.fallback == Decimal("0.318524")
    assert {period.price for period in version.periods} == {
        Decimal("0.106175"),
        Decimal("0.955573"),
    }


def _area(key: str, name: str, datahub: str | None = None):  # type: ignore[no-untyped-def]
    return elpris_dk.parse(
        _json("elpris_dk", f"distributionAreaCharge_{key}.json"),
        _json("elpris_dk", "nationalCharges.json"),
        _json("datahub", datahub)["records"] if datahub else [],
        area=key,
        name=name,
        fetched=date(2026, 9, 26),
    ).grid


def test_3_a_household_is_billed_flex_not_the_flat_fix_record() -> None:
    """Aars-Hornum lists AARS-NT-01 as `flex` and as `fix` (no hours), and 50001 beside it (D-0682)."""
    assert elpris_dk.charge_code(_json("elpris_dk", "distributionAreaCharge_014.json")) == (
        "AARS-NT-01"
    )
    grid = _area("014", "Aars-Hornum El-forsyning")
    assert [version.valid_from for version in grid.energy] == [date(2023, 11, 1)]


def test_3_an_hour_priced_twice_is_read_from_datahub_not_guessed() -> None:
    """Elinord's 43300 on elpris.dk is Læsø's rows and Elinord's merged; Datahub says flat."""
    with pytest.raises(QualityError, match="more than once"):
        _area("051", "Elinord A/S")
    records = _json("datahub", "43300-from-2026-02-01.json")["records"]
    elinord = datahub_pricelist.owned(records, "Elinord A/S", "051")
    assert {row["ChargeOwner"] for row in elinord} == {"Elinord A/S"}
    grid = elpris_dk.parse(
        _json("elpris_dk", "distributionAreaCharge_051.json"),
        _json("elpris_dk", "nationalCharges.json"),
        elinord,
        area="051",
        name="Elinord A/S",
        fetched=date(2026, 9, 26),
    ).grid
    (version,) = grid.energy
    evening = datetime(2026, 9, 28, 18, tzinfo=COPENHAGEN)
    assert _price_at(version, evening) == Decimal("0.1864") + ENERGINET


def test_3_datahubs_owner_is_found_by_its_own_spelling() -> None:
    """One owner is the code's; of several, elpris.dk's name, the area-suffixed one first."""
    rows = [
        {"ChargeOwner": "Konstant Net A/S - 245"},
        {"ChargeOwner": "Konstant Net A/S - 151"},
    ]
    assert datahub_pricelist.owned(rows, "KONSTANT Net A/S", "151") == [rows[1]]
    assert datahub_pricelist.owned([{"ChargeOwner": "L-Net A/S"}], "L-NET", "351")
    assert not datahub_pricelist.owned(rows, "Elinord A/S", "051"), "no namesake, no rows"


def test_3_without_flex_hours_datahubs_rows_are_the_tariff() -> None:
    """Zeanet lists 43110 only as `fix` at 0.0719, the night rate; Datahub has the table."""
    grid = _area("860", "Zeanet A/S", "zeanet-43110.json")
    assert [version.valid_from for version in grid.energy] == [date(2026, 9, 1), date(2026, 10, 1)]
    september, winter = grid.energy
    night = datetime(2026, 9, 28, 3, tzinfo=COPENHAGEN)
    assert _price_at(september, night) == Decimal("0.0719") + ENERGINET
    assert _price_at(september, night.replace(hour=18)) == Decimal("0.2803") + ENERGINET
    assert _price_at(winter, night.replace(month=11, hour=18)) == Decimal("0.6468") + ENERGINET
    with pytest.raises(QualityError, match="no hourly tariff"):
        _area("860", "Zeanet A/S")


def test_18_a_table_priced_in_hour_0_alone_is_that_price_all_day() -> None:
    """Forsyning Elnet's STR-NT-02: 0,0375 in hour 0 and 0 after is 0,0375 every hour (D-0703)."""
    fetched = elpris_dk.parse(
        _json("elpris_dk", "distributionAreaCharge_357.json"),
        _json("elpris_dk", "nationalCharges.json"),
        [],
        area="357",
        name="Forsyning Elnet A/S",
        fetched=date(2026, 9, 27),
    )
    version = fetched.grid.energy[-1]
    prices = {period.price for period in version.periods} | {version.fallback}
    assert prices == {Decimal("0.0375") + ENERGINET}
    assert version.valid_from == date(2026, 1, 1)
    # equal to Datahub's row, `Price1` with the other hours empty
    assert (
        elpris_dk.day_prices({0: Decimal("0.0375"), **{h: Decimal(0) for h in range(1, 24)}})
        == [Decimal("0.0375")] * 24
    )


def test_18_a_table_with_two_hours_priced_is_read_as_it_is() -> None:
    """Only hour 0 alone is Datahub's convention; a night rate from 01:00 stays."""
    hours = {h: Decimal(0) for h in range(24)} | {0: Decimal("0.2"), 1: Decimal("0.1")}
    assert elpris_dk.day_prices(hours)[:3] == [Decimal("0.2"), Decimal("0.1"), Decimal(0)]


def _household(owner: str, area: str, fetched: date) -> Any:
    """Parse an area elpris.dk lists no tariff for, with its owner's Datahub rows."""
    records = _json("datahub", "nettarif-c-from-2025-01-01.json")["records"]
    return elpris_dk.parse(
        _json("elpris_dk", f"distributionAreaCharge_{area}.json"),
        _json("elpris_dk", "nationalCharges.json"),
        datahub_pricelist.owned(records, owner, area),
        area=area,
        name=owner,
        fetched=fetched,
    ).grid


def test_22_an_area_elpris_lists_no_tariff_for_is_billed_its_owners_nettarif_c() -> None:
    """Tarm lists only its subscription from 1 October: Datahub's TEV-NT-01T is the tariff (D-0707)."""
    records = _json("datahub", "nettarif-c-from-2025-01-01.json")["records"]
    tarm = datahub_pricelist.owned(records, "Tarm Elværk Net A/S", "384")
    assert {row["ChargeOwner"] for row in tarm} == {"Tarm Elværk Net A/S"}
    # TEV-NT-01, priced 0 in every hour, ends the day TEV-NT-01T begins
    assert datahub_pricelist.code_at(tarm, date(2026, 9, 24)) == "TEV-NT-01T"
    grid = _household("Tarm Elværk Net A/S", "384", date(2026, 10, 4))
    (version,) = grid.energy
    assert version.valid_from == date(2026, 10, 1)
    peak = datetime(2026, 10, 5, 17, tzinfo=COPENHAGEN)
    assert _price_at(version, peak) == Decimal("0.5986") + ENERGINET
    assert _price_at(version, peak.replace(hour=3)) == Decimal("0.0665") + ENERGINET
    assert grid.valid_to == date(2026, 12, 31)
    assert grid.fixed_fee[0].amount == Decimal(77) + Decimal("15.5833")
    with pytest.raises(QualityError, match="no hourly tariff"):
        _area("384", "Tarm Elværk Net A/S")


def test_22_every_season_datahub_has_registered_follows() -> None:
    """FLOW lists no tariff either; FE1 NT-01's autumn and winter seasons are the copy."""
    grid = _household("FLOW Elnet A/S", "533", date(2026, 10, 4))
    assert [version.valid_from for version in grid.energy] == [date(2026, 10, 1), date(2026, 12, 1)]
    winter = datetime(2027, 1, 4, 17, tzinfo=COPENHAGEN)
    assert _price_at(grid.energy[-1], winter) == Decimal("0.5567") + ENERGINET
    assert grid.valid_to == date(2027, 3, 31)
