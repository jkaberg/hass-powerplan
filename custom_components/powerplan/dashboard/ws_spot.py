"""websocket: powerplan/spot_prices - spot curve + fixed-price (Norgespris) effect for the price card.

The price card's command (D12 §5.15 F5), answered from the site's own runtime instead of a second
Nord Pool call (D-0582): the spot is each slot's `spot` component on the curve without the fixed price
(`Runtime.reference_curve`, D-0495), the fixed price is the `FixedPrice` modifier's own price with
the VAT that covers it - never anything derived from a price slot's `energy` field - and the effect
is the month's `FixedPriceSaving` (D-0499).

Seen on the reference house: the Strømpris split said "Kraft 0,45 · Norgespris / Nettleie 0,43" while
sensor.strom_kraftledd = 0,50 and sensor.nettleie_energiledd = 0,3779 (sum = the price 0,8779).
`energy_field_check` in the response shows the difference between the current slot's `energy` and
the fixed price; 0 since D-0581.

Request   {"type": "powerplan/spot_prices", "entry_id": "<powerplan entry id>"}
Response  {
  "area": "NO3", "currency": "NOK", "vat": 0.25, "fixed_price": 0.5,        # per kWh incl. VAT, or null
  "slots": [{"start": iso, "end": iso, "spot": 1.043},...],                  # today + tomorrow, per kWh excl. VAT
  "tomorrow_available": true,
  "effect": {"today_kwh": 43.3, "today_nok": 43.5, "month_kwh": 1191.5, "month_nok": 991.0}  # or null
}
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.websocket_api import async_register_command
from homeassistant.components.websocket_api.decorators import websocket_command
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.model import Carrier, Confidence
from custom_components.powerplan.core.pricing.modifiers.base import SPOT
from custom_components.powerplan.core.pricing.modifiers.fixed_price import FixedPrice
from custom_components.powerplan.core.pricing.modifiers.vat import energy_vat_rate

if TYPE_CHECKING:
    from homeassistant.components.websocket_api.connection import ActiveConnection

    from custom_components.powerplan.runtime import Runtime

__all__ = ["async_register_ws", "spot_prices"]


def spot_prices(runtime: Runtime, now: datetime) -> dict[str, Any]:
    """Return the price card's spot data for one site; plain data in, plain data out."""
    build = runtime.build
    vat = energy_vat_rate(build.price_modifiers)
    fixed = next((m for m in build.price_modifiers if isinstance(m, FixedPrice)), None)
    fixed_price = None if fixed is None else float(round(fixed.price * (1 + vat), 5))
    curves = runtime.curves
    actual = None if curves is None else curves.import_.get(Carrier.ELECTRICITY)
    spot_curve = runtime.reference_curve if fixed is not None else actual
    tz = build.cfg.tz
    local = now.astimezone(tz)
    today = datetime(local.year, local.month, local.day, tzinfo=tz)
    tomorrow = today + timedelta(days=1)
    end = today + timedelta(days=2)
    slots: list[dict[str, Any]] = []
    tomorrow_available = False
    for slot in () if spot_curve is None else spot_curve.slots:
        spot = slot.components.get(SPOT)
        if spot is None or slot.end <= today or slot.start >= end:
            continue
        slots.append(
            {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "spot": float(round(spot, 5)),
            }
        )
        if slot.start >= tomorrow and slot.confidence is Confidence.KNOWN:
            tomorrow_available = True
    check = None
    if fixed_price is not None and actual is not None:
        current = next((s for s in actual.slots if s.start <= now < s.end), None)
        if current is not None and (spot := current.components.get(SPOT)) is not None:
            check = round(float(spot * (1 + vat)) - fixed_price, 4)
    saving = runtime.fixed_saving
    effect = (
        None
        if fixed is None or saving is None
        else {
            "today_kwh": saving.today_kwh,
            "today_nok": saving.today,
            "month_kwh": saving.kwh,
            "month_nok": round(saving.month),
        }
    )
    return {
        "area": next(
            (area for source in build.sources if (area := getattr(source, "area", None))), None
        ),
        "currency": build.cfg.currency,
        "vat": float(vat) if vat else float(Decimal(0)),
        "fixed_price": fixed_price,
        "fixed_price_source": None if fixed is None else "fixed_price",
        "energy_field_check": check,
        "slots": slots,
        "tomorrow_available": tomorrow_available,
        "effect": effect,
    }


@websocket_command({vol.Required("type"): "powerplan/spot_prices", vol.Required("entry_id"): str})
@callback
def ws_spot_prices(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    """Answer one site's spot data."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_found", "PowerPlan entry not found")
        return
    connection.send_result(msg["id"], spot_prices(entry.runtime_data, dt_util.utcnow()))


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    """Register the command once per HA."""
    async_register_command(hass, ws_spot_prices)
