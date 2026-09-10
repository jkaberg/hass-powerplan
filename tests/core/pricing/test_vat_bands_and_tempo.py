"""D1 §9 21, 22 - Portugal's VAT bands (G17) and Tempo's colour × hour (G12).

21: the first 200 kWh of a month at 6 %, the 201st at 23 %; 300 for a household
of five or more; nothing reduced above 6.9 kVA; the Azores' own 4 %. 22: a red
day's HP and HC at their own prices, and the colour announced for a day prices
it from 06:00 to the next 06:00.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.model import Confidence, Slot
from custom_components.powerplan.core.pricing import party
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from custom_components.powerplan.core.pricing.modifiers.day_type import DayType
from custom_components.powerplan.core.tariffs import ContractedPower, NoPeak, PeriodLimit
from custom_components.powerplan.core.tariffs.household import (
    EXCL,
    LARGE_HOUSEHOLD,
    EnergyVersion,
    GridTariff,
    HouseholdPrice,
    Provenance,
    StateTerms,
    SupplierContract,
    TaxZone,
)
from custom_components.powerplan.core.tariffs.model import TariffVersion
from tests.builders.curves import context

LISBON = ZoneInfo("Europe/Lisbon")
PARIS = ZoneInfo("Europe/Paris")


def _portugal(
    *, zone: str | None = None, kva: float | None = None, large: bool = False
) -> HouseholdPrice:
    rules: tuple = (NoPeak(),)  # type: ignore[type-arg]
    if kva is not None:
        rules = (
            *rules,
            ContractedPower(limits=(PeriodLimit(when=None, limit_kw=kva),), on_exceed="trip"),
        )
    grid = GridTariff(
        operator="E-Redes",
        product="BTN simples",
        provenance=Provenance(source="custom"),
        currency="EUR",
        basis=EXCL,
        capacity=(TariffVersion(valid_from=date(2026, 1, 1), version_id="pt", rules=rules),),
        energy=(EnergyVersion(valid_from=date(2026, 1, 1), periods=(), fallback=Decimal("0.05")),),
    )
    return HouseholdPrice(
        grid=grid,
        supplier=SupplierContract(),
        state=StateTerms(TaxZone("PT", zone)),
        confirmed={LARGE_HOUSEHOLD: True} if large else {},
    )


def _vat(price: HouseholdPrice, mtd_kwh: float) -> Decimal:
    """Return the VAT fraction a slot pays with `mtd_kwh` already this month."""
    chain, _ = party.chain(price, (), frozenset({"spot"}))
    when = datetime(2026, 9, 24, 12, tzinfo=LISBON)
    slot = Slot(
        start=when,
        end=when + timedelta(hours=1),
        total=Decimal("0.10"),
        components={SPOT: Decimal("0.10")},
        confidence=Confidence.KNOWN,
    )
    ctx = context(when, tz=LISBON, currency="EUR", mtd_kwh=mtd_kwh)
    for modifier in chain:
        slot = modifier.apply(slot, ctx)
    return slot.components["vat"] / (slot.total - slot.components["vat"])


@pytest.mark.parametrize(
    ("mtd", "large", "rate"),
    [
        (0.0, False, "0.06"),
        (199.0, False, "0.06"),
        (200.0, False, "0.23"),
        (250.0, False, "0.23"),
        (250.0, True, "0.06"),
        (300.0, True, "0.23"),
    ],
)
def test_21_the_first_200_kwh_of_a_month_pay_six_percent(
    mtd: float, large: bool, rate: str
) -> None:
    """The 200th kWh at 6 %, the 201st at 23 %; 300 for a household of five or more."""
    assert _vat(_portugal(large=large), mtd) == Decimal(rate)


def test_21_above_six_point_nine_kva_nothing_is_reduced() -> None:
    """10.35 kVA pays 23 % on its first kWh; 6.9 kVA still gets the band."""
    assert _vat(_portugal(kva=10.35), 0.0) == Decimal("0.23")
    assert _vat(_portugal(kva=6.9), 0.0) == Decimal("0.06")


def test_21_the_azores_band_is_four_percent_and_its_full_rate_sixteen() -> None:
    """A region's own band and its own full rate."""
    assert _vat(_portugal(zone="azores"), 10.0) == Decimal("0.04")
    assert _vat(_portugal(zone="azores"), 210.0) == Decimal("0.16")


def test_21_a_vat_override_beats_the_band() -> None:
    """A household that states its VAT (a registered business) pays that, band or not."""
    price = _portugal()
    price = replace(price, state=replace(price.state, overrides={"vat": Decimal(0)}))
    assert _vat(price, 0.0) == 0


# --------------------------------------------------------------------------- #
# 22 - Tempo (G12)
# --------------------------------------------------------------------------- #

HP = {"hours": [[6 * 60, 22 * 60]]}
TEMPO = DayType.from_options(
    {
        "rates": [
            {"type": "blue", "periods": [HP | {"price": "0.1552"}, {"price": "0.1288"}]},
            {"type": "white", "periods": [HP | {"price": "0.1792"}, {"price": "0.1447"}]},
            {"type": "red", "periods": [HP | {"price": "0.6586"}, {"price": "0.1518"}]},
        ],
        "fallback": "blue",
        "day_starts_min": 360,
    }
)
DAYS = {date(2026, 12, 3): "red", date(2026, 12, 4): "white"}


def _tempo(local: datetime) -> Decimal:
    slot = Slot(
        start=local,
        end=local + timedelta(minutes=15),
        total=Decimal(0),
        components={SPOT: Decimal(0)},
        confidence=Confidence.KNOWN,
    )
    ctx = context(local, tz=PARIS, currency="EUR", day_types=DAYS)
    return TEMPO.apply(slot, ctx).components["day_type"]


@pytest.mark.parametrize(
    ("local", "price"),
    [
        (datetime(2026, 12, 3, 5, 45), "0.1288"),  # still the 2nd's day: unannounced, blue HC
        (datetime(2026, 12, 3, 6, 0), "0.6586"),  # red from 06:00: HP
        (datetime(2026, 12, 3, 21, 45), "0.6586"),
        (datetime(2026, 12, 3, 22, 0), "0.1518"),  # red HC
        (datetime(2026, 12, 4, 5, 45), "0.1518"),  # after midnight: still the red day's HC
        (datetime(2026, 12, 4, 6, 0), "0.1792"),  # white from 06:00
    ],
)
def test_22_a_red_days_hp_and_hc_run_from_six_to_six(local: datetime, price: str) -> None:
    """Colour × hour: six prices, and a colour's day starts at 06:00, not midnight."""
    assert _tempo(local.replace(tzinfo=PARIS)) == Decimal(price)
