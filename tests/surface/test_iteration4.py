"""D12 §9 24, 27 - the backend behind the price and appliance cards: spot prices from the runtime, honest savings, the price refresher.

The spot comes from the site's own curves (D-0582), published on `price_forecast` (§5.16 R2, D-0621), a
load's savings are unknown without a reference (D-0583), and the price refresher retries and raises
`prices_stale` after 30 minutes (D-0580).
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
from custom_components.powerplan.price_refresh import BACKOFF_S, PriceRefresher
from custom_components.powerplan.runtime import FixedPriceSaving
from custom_components.powerplan.savings_guard import NO_REFERENCE, guarded_savings, house_total
from custom_components.powerplan.sensor import _fixed_price, _slots

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


def _spot_rows(runtime: SimpleNamespace) -> list[dict[str, object]]:
    """`price_forecast`'s slots, as `sensor.py` publishes them."""
    return _slots(
        runtime.curves.import_[Carrier.ELECTRICITY],
        reference=runtime.reference_curve,
        energy_vat=energy_vat_rate(runtime.build.price_modifiers),
    )


def test_27_the_spot_comes_from_the_curve_without_the_fixed_price() -> None:
    """F5, D-0582, §5.16 R2: Norgespris 0,40 + 25 % → 0,50; each slot's spot is the reference curve's."""
    runtime = _runtime(fixed=True)
    rows = _spot_rows(runtime)
    assert _fixed_price(runtime) == 0.5
    assert {row["spot"] for row in rows} == {0.834}
    # v0.7's `energy_field_check`: the slot's energy part is the fixed price with its VAT.
    assert {row["energy"] for row in rows} == {"0.50000"}
    assert len(rows) == 48


def test_27_the_spot_without_a_fixed_price_is_the_curves_own() -> None:
    """No fixed price: the spot is the import curve's, and `fixed_price` is null."""
    runtime = _runtime(fixed=False, known_until_h=13)
    assert _fixed_price(runtime) is None
    assert {row["spot"] for row in _spot_rows(runtime)} == {0.4}
    assert all("reference" not in row for row in _spot_rows(runtime))


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
