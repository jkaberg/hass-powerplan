"""D12 §9 24 - the price card's backend: spot prices from the runtime, honest savings, the price refresher.

D12 §5.15 in the integration's shape: `powerplan/spot_prices` answers from
the site's own curves (D-0582), a load's savings are unknown without a reference (D-0583), and the
price refresher retries and raises `prices_stale` after 30 minutes (D-0580).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.model import Carrier, Confidence, Direction, PriceCurve, Slot
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.pricing.modifiers.vat import Vat, energy_vat_rate
from custom_components.powerplan.dashboard.ws_spot import spot_prices
from custom_components.powerplan.price_refresh import BACKOFF_S, PriceRefresher
from custom_components.powerplan.runtime import FixedPriceSaving
from custom_components.powerplan.savings_guard import NO_REFERENCE, guarded_savings, house_total

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 9, 24, 9, 5, tzinfo=UTC)  # 11:05 in Oslo


def _curve(
    spots: list[str], total: str, *, from_h: int = -11, known_until_h: int = 48
) -> PriceCurve:
    start = datetime(2026, 9, 24, 9, tzinfo=UTC) + timedelta(hours=from_h)
    slots = tuple(
        Slot(
            start=start + timedelta(hours=i),
            end=start + timedelta(hours=i + 1),
            total=Decimal(total),
            components={"spot": Decimal(spot)},
            confidence=Confidence.KNOWN if i + from_h < known_until_h else Confidence.SYNTHESISED,
        )
        for i, spot in enumerate(spots)
    )
    return PriceCurve(
        carrier=Carrier.ELECTRICITY,
        direction=Direction.IMPORT,
        currency="NOK",
        slots=slots,
        built_at=start,
        sources=("nordpool",),
    )


def _runtime(*, fixed: bool, known_until_h: int = 48) -> SimpleNamespace:
    modifiers = [Vat(rate=Decimal("0.25"), applies_to=("spot",))]
    if fixed:
        modifiers.insert(0, FixedPrice(price=Decimal("0.40")))
    hours = 11 + 24 + 13  # local midnight today → local midnight the day after tomorrow, and a bit
    actual = _curve(["0.40"] * hours, "0.8779", known_until_h=known_until_h)
    reference = _curve(["0.834"] * hours, "1.3204", known_until_h=known_until_h)
    return SimpleNamespace(
        build=SimpleNamespace(
            price_modifiers=tuple(modifiers),
            sources=(SimpleNamespace(area="NO3"),),
            cfg=SimpleNamespace(tz=OSLO, currency="NOK"),
        ),
        curves=SimpleNamespace(import_={Carrier.ELECTRICITY: actual}),
        reference_curve=reference if fixed else None,
        fixed_saving=FixedPriceSaving(
            since=NOW, month=991.2, today=43.5, kwh=1191.5, today_kwh=43.3
        ),
    )


def test_24_spot_prices_come_from_the_curve_without_the_fixed_price() -> None:
    """F5, D-0582: Norgespris 0,40 + 25 % → 0,50; the spot is the reference curve's; today and tomorrow."""
    answer = spot_prices(_runtime(fixed=True), NOW)
    assert answer["area"] == "NO3"
    assert answer["vat"] == 0.25
    assert answer["fixed_price"] == 0.5
    assert answer["energy_field_check"] == 0.0
    assert {slot["spot"] for slot in answer["slots"]} == {0.834}
    # 00:00 local today (22:00 UTC) to 00:00 local the day after tomorrow: 48 hours.
    assert answer["slots"][0]["start"] == "2026-09-23T22:00:00+00:00"
    assert len(answer["slots"]) == 48
    assert answer["tomorrow_available"] is True
    assert answer["effect"] == {
        "today_kwh": 43.3,
        "today_nok": 43.5,
        "month_kwh": 1191.5,
        "month_nok": 991,
    }


def test_24_spot_prices_without_a_fixed_price_or_tomorrow() -> None:
    """No fixed price: the spot is the import curve's, no effect; tomorrow not in yet."""
    answer = spot_prices(_runtime(fixed=False, known_until_h=13), NOW)
    assert answer["fixed_price"] is None
    assert answer["effect"] is None
    assert {slot["spot"] for slot in answer["slots"]} == {0.4}
    assert answer["tomorrow_available"] is False


def test_24_the_energy_vat_is_the_vat_that_covers_the_energy() -> None:
    """D-0581: a VAT on the grid only adds nothing to the energy part."""
    assert energy_vat_rate([Vat(rate=Decimal("0.25"))]) == Decimal("0.25")
    assert energy_vat_rate([Vat(rate=Decimal("0.25"), applies_to=("grid",))]) == 0
    assert energy_vat_rate([FixedPrice(price=Decimal("0.40"))]) == 0


def test_24_savings_are_unknown_without_a_reference() -> None:
    """F10, D-0583: loads with no reference never read exactly −cost; a real loss stays a loss."""
    assert guarded_savings(0.59, 0.0).value is None
    assert guarded_savings(0.59, 0.0).attributes == {"reason": NO_REFERENCE}
    assert guarded_savings(0.59, None).reason == NO_REFERENCE
    assert guarded_savings(0.79, 0.60).value == -0.19
    assert guarded_savings(0.0, 0.0).value == 0.0
    total = house_total([guarded_savings(0.59, 0.0), guarded_savings(1.0, 1.5)])
    assert total.value == 0.5
    assert total.reason == "1_loads_without_reference"


async def test_24_the_price_refresher_retries_and_says_so_after_30_minutes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """F12, D-0580: back-off while now's slot is unknown; `prices_stale` at 30 min; cleared when known."""
    freezer.move_to(NOW)
    known = {"now": False}
    fetches: list[datetime] = []

    async def refresh() -> bool:
        fetches.append(datetime.now(UTC))
        return True

    entry = SimpleNamespace(entry_id="site", title="Home")
    refresher = PriceRefresher(hass, entry, refresh, lambda: known["now"])  # type: ignore[arg-type]
    refresher.async_setup()
    refresher.observe("startup")
    issue_id = "site_prices_stale"
    elapsed = 0
    for delay in (*BACKOFF_S, BACKOFF_S[-1]):
        elapsed += delay
        freezer.tick(timedelta(seconds=delay))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        stale = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
        assert (stale is not None) == (elapsed >= 30 * 60), elapsed
    assert len(fetches) == len(BACKOFF_S) + 1
    known["now"] = True
    assert await refresher.async_refresh("user") is True
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    refresher.async_unload()
