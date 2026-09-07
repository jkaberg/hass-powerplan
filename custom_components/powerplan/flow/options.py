"""The options flow: the state overrides of a household that knows better (D13 O4, D8 §5.17)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from custom_components.powerplan.const import CONF_TARIFF
from custom_components.powerplan.core.tariffs import household

from . import steps
from .questionnaire import price_shown, price_stored

__all__ = ["StateOverridesFlow"]


class StateOverridesFlow(OptionsFlow):
    """VAT and levies for a household that knows better - a VAT-registered farm (O4).

    Empty keeps the country module's own rate; a value replaces it in the copy's
    state party. Written to the site's data, which the site reloads on.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the two overrides, pre-filled with the site's own."""
        entry = self.config_entry
        tariff = dict(entry.data.get(CONF_TARIFF) or {})
        if not tariff.get("price"):
            return self.async_abort(reason="no_tariff")
        price = household.from_json(tariff["price"])
        currency = price.grid.currency or self.hass.config.currency
        overrides = price.state.overrides
        if user_input is None:
            shown = {
                "vat": None if "vat" not in overrides else float(overrides["vat"] * 100),
                "levy": None
                if "levy" not in overrides
                else price_shown(overrides["levy"], currency),
            }
            return self.async_show_form(
                step_id="init", data_schema=steps.overrides_schema(shown, currency)
            )
        kept = {key: value for key, value in overrides.items() if key not in ("vat", "levy")}
        if user_input.get("vat") is not None:
            kept["vat"] = Decimal(str(user_input["vat"])) / 100
        if user_input.get("levy") is not None:
            levy = price_stored(user_input["levy"], currency)
            if levy is not None:
                kept["levy"] = levy
        price = replace(price, state=replace(price.state, overrides=kept))
        tariff.update(price=household.to_json(price), review=[])
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TARIFF: tariff})
        return self.async_create_entry(data=dict(entry.options))
