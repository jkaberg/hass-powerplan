"""D11 §9 21, 22 - savings by party (D11 §5.8, D13 §7).

The curves here are composed by the site's real chain (D1 §5.3): Tensio TS's copy
for the grid, Norgespris or spot for the supplier, Norway's VAT and levies for the
state. The EV of §9 10 plugs in at 17:00 and charges at 22:00–23:59 on the grid's
night rate; its shadow charges at once, on the day rate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.powerplan.core.accounting import (
    LoadParams,
    StoreKind,
    cost_by_party,
    savings_by_party,
)
from custom_components.powerplan.core.model import (
    Carrier,
    Confidence,
    Direction,
    Money,
    PriceCurve,
    Slot,
)
from custom_components.powerplan.core.pricing import party
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.tariffs import (
    ContractedPower,
    Evaluator,
    NoPeak,
    PeriodLimit,
)
from custom_components.powerplan.core.tariffs.household import TaxZone, from_preset
from custom_components.powerplan.core.tariffs.rules import loader
from tests.builders.curves import context
from tests.builders.presets import fixture_raw
from tests.core.accounting.conftest import (
    OSLO,
    Site,
    closed_slot,
    config,
    curve,
    demand,
    local,
    no3,
    shadow_ctx,
    site,
    window,
)
from tests.core.tariffs.conftest import Holidays
from tests.core.tariffs.conftest import spec as inline_spec

MAX_W = 11000.0
DAY = date(2026, 9, 15)


def composed(
    first: date, days: int, *, norgespris: bool, zone: TaxZone | None = None
) -> PriceCurve:
    """Return hourly slots over `days` local days, composed by party."""
    if zone is None or zone.country == "NO":
        raw = fixture_raw("no/tensio-ts")
        price = from_preset(raw, source="shipped", zone=zone or TaxZone("NO"))
    else:
        raw = loader.load_raw("uk/nopeak") | {"currency": "GBP"}
        price = from_preset(raw, source="template", zone=zone, typed=True)
    added = (FixedPrice(price=Decimal("0.40")),) if norgespris else ()
    chain, _ = party.chain(price, added, frozenset({"spot"}))
    start = datetime.combine(first, datetime.min.time(), tzinfo=OSLO).astimezone(UTC)
    slots = []
    for hour in range(days * 24):
        when = start + timedelta(hours=hour)
        spot = no3(when.astimezone(OSLO).hour)
        slot = Slot(
            start=when,
            end=when + timedelta(hours=1),
            total=spot,
            components={"spot": spot},
            confidence=Confidence.KNOWN,
        )
        ctx = context(when)
        for modifier in chain:
            slot = modifier.apply(slot, ctx)
        slots.append(slot)
    return PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=tuple(slots),
        built_at=start,
        sources=("test",),
    )


def _ev_evening(norgespris: bool) -> dict[str, Decimal]:
    under_test = site(import_curve=composed(DAY, 2, norgespris=norgespris))
    under_test.with_load(
        "ev",
        shadow_ctx(
            params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W),
            demand=demand(wants=True, required_kwh=20.0, max_w=MAX_W),
        ),
    )
    for hour in (17, 18, 19, 22, 23):
        under_test.close(
            closed_slot(
                local(2026, 9, 15, hour, 0).astimezone(UTC),
                loads={"ev": 11.0 if hour == 22 else (9.0 if hour == 23 else 0.0)},
                uncontrolled_kwh=2.0,
            )
        )
    # The car is done: the session settles, and its savings with it (D11 §5.9).
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    under_test.close(
        closed_slot(
            local(2026, 9, 16, 0, 0).astimezone(UTC), loads={"ev": 0.0}, uncontrolled_kwh=2.0
        )
    )
    status = under_test.accounting.status()
    split = savings_by_party(
        under_test.accounting.state().ledger.site,
        under_test.accounting.state().ledger.loads.values(),
    )
    assert sum(amount.amount for amount in split.values()) == status.site.savings.amount
    return {key: amount.amount for key, amount in split.items()}


def test_21_a_norgespris_month_saves_on_the_grids_charge_and_nothing_on_the_supplier() -> None:
    """The supplier's price is flat: every øre saved is the grid's night rate, with its VAT.

    Levies are the same per kWh by day and night and cancel; the VAT on the grid's
    difference is the state's line - 25 % of the grid party's (D11 §5.8).
    """
    split = _ev_evening(norgespris=True)
    assert split["supplier"] == 0
    assert split["grid"] > 0
    assert split["state"] == split["grid"] * Decimal("0.25")


def test_21_a_spot_month_splits_between_the_grid_and_the_supplier() -> None:
    """On spot, the evening costs more on both the grid and the supplier: each has its share."""
    split = _ev_evening(norgespris=False)
    assert split["grid"] > 0
    assert split["supplier"] != 0
    assert split["state"] == (split["grid"] + split["supplier"]) * Decimal("0.25")


def test_22_a_vat_change_mid_month_moves_both_worlds_and_saves_nothing() -> None:
    """GB goes to 0 % on 2026-10-01: the cost falls, and no saving appears from it."""
    gb = TaxZone("GB")
    prices = composed(date(2026, 9, 30), 2, norgespris=False, zone=gb)
    assert prices.slots[12].components["vat"] > 0, "30 September: 5 %"
    assert prices.slots[-12].components["vat"] == 0, "1 October: 0 %"
    under_test = site(import_curve=prices)
    under_test.with_load("pump", shadow_ctx(params=LoadParams(kind=StoreKind.NONE)))
    first = datetime.combine(date(2026, 9, 30), datetime.min.time(), tzinfo=OSLO).astimezone(UTC)
    for hour in range(48):
        under_test.close(closed_slot(first + timedelta(hours=hour), loads={"pump": 1.0}))
    ledger = under_test.accounting.state().ledger
    rec = ledger.loads["pump"]
    assert rec.cf_cost == rec.cost
    assert rec.savings.amount == 0
    assert all(amount.amount == 0 for amount in savings_by_party(ledger.site, [rec]).values())


def test_the_months_cost_by_party_sums_to_its_cost() -> None:
    """The energy by its components, the capacity fee the grid's: the three are the cost."""
    under_test = site(import_curve=composed(DAY, 1, norgespris=True))
    for hour in range(24):
        under_test.close(
            closed_slot(local(2026, 9, 15, hour, 0).astimezone(UTC), uncontrolled_kwh=1.5)
        )
    ledger = under_test.accounting.state().ledger
    split = cost_by_party(ledger.site)
    assert sum(money.amount for money in split.values()) == ledger.site.cost.amount
    # Norgespris: the supplier's energy is 0.40 NOK/kWh ex VAT on each kWh.
    assert split["supplier"].amount == Decimal("0.40") * Decimal("1.5") * 24
    status = under_test.accounting.status()
    assert status.site.cost_by_party == split
    assert set(status.site.savings_by_party) == {"grid", "supplier", "state"}


# --------------------------------------------------------------------------- #
# D11 §9 23 (O23) - LU's energy surcharge in both worlds
# --------------------------------------------------------------------------- #

LUX = ZoneInfo("Europe/Luxembourg")


def _lu_site() -> Site:
    """Return a LU house: no peak, 7 kW reference power at 0.0765 €/kWh above it, 15-min windows."""
    power = ContractedPower(
        limits=(PeriodLimit(when=None, limit_kw=7.0),),
        on_exceed="energy_surcharge",
        surcharge_per_kwh=Money(Decimal("0.0765"), "EUR"),
    )
    tariff = Evaluator(inline_spec(NoPeak(), power, currency="EUR"), LUX, Holidays())
    assert tariff.history.window_min == 15
    prices = curve(DAY, 1, shape=lambda _h: Decimal("0.20"), minutes=15, tz=LUX, currency="EUR")
    under_test = site(tariff=tariff, import_curve=prices, cfg=config(currency="EUR", tz=LUX))
    under_test.tz = LUX
    return under_test


def test_23_actual_and_counterfactual_each_carry_their_own_surcharge() -> None:
    """The EV charged at 5.5 kW under the limit; its shadow at once at 11 kW, over it.

    The counterfactual's four windows are 12 kW: 5 kW over × ¼ h × 0.0765 each.
    The actual's are 6.5 kW: none. The difference is the grid party's saving.
    """
    under_test = _lu_site()
    under_test.with_load(
        "ev",
        shadow_ctx(
            params=LoadParams(kind=StoreKind.ENERGY, nameplate_w=MAX_W, max_w=MAX_W),
            demand=demand(wants=True, required_kwh=11.0, max_w=MAX_W),
        ),
    )
    first = datetime(2026, 9, 15, 17, tzinfo=LUX).astimezone(UTC)
    for index in range(8):
        start = first + timedelta(minutes=15 * index)
        closed = window(start, 1.375 + 0.25, window_min=15)
        under_test.close(
            closed_slot(
                start, minutes=15, loads={"ev": 1.375}, uncontrolled_kwh=0.25, window_closed=closed
            )
        )
    # The charge is done: the session settles, and its windows with it (D11 §5.9.4).
    under_test.ctx_for("ev", demand=demand(wants=False, max_w=MAX_W))
    end = first + timedelta(hours=2)
    under_test.close(
        closed_slot(
            end,
            minutes=15,
            loads={"ev": 0.0},
            uncontrolled_kwh=0.25,
            window_closed=window(end, 0.25, window_min=15),
        )
    )
    ledger = under_test.accounting.state().ledger
    expected = Decimal(4) * Decimal("5.0") * Decimal("0.25") * Decimal("0.0765")
    assert ledger.site.surcharge == 0
    assert ledger.site.cf_surcharge == pytest.approx(expected)
    split = savings_by_party(ledger.site, ledger.loads.values())
    assert ledger.site.capacity_savings.amount == pytest.approx(expected)
    assert split["grid"].amount >= ledger.site.capacity_savings.amount - Decimal("1e-9")


# --------------------------------------------------------------------------- #
# D4 §5.16, G13 - a load billed on its own meter
# --------------------------------------------------------------------------- #


def test_a_load_on_its_own_tariff_is_billed_at_it_and_the_site_with_it() -> None:
    """A heat pump on a 0.10 tariff under a 1.00 house: 2 kWh cost 0.20, the site 1 + 0.20.

    The site's line takes the pump's kWh off the house's price and onto its own,
    so the site's cost is what the two bills say together.
    """
    house = curve(DAY, 1, shape=lambda _h: Decimal("1.00"))
    own = curve(DAY, 1, shape=lambda _h: Decimal("0.10"))
    under_test = site(import_curve=house)
    under_test.with_load("hp", shadow_ctx(params=LoadParams(kind=StoreKind.NONE)))
    start = local(2026, 9, 15, 12, 0).astimezone(UTC)
    under_test.close(
        closed_slot(start, loads={"hp": 2.0}, uncontrolled_kwh=1.0),
        load_curves={"hp": own},
    )
    ledger = under_test.accounting.state().ledger
    assert ledger.loads["hp"].cost.amount == Decimal("0.20")
    assert ledger.site.energy_cost.amount == Decimal("1.20")
    assert sum((m.amount for m in cost_by_party(ledger.site).values()), Decimal(0)) == Decimal(
        "1.20"
    )
