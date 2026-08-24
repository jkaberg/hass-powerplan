"""The site's own reconfigure flow (D8 §5.1).

The gear the site entry itself was missing - every subentry (load, circuit,
group, zone) already has one. `async_step_reconfigure` restores the flow's own
instance state from
`entry.data` - `_assemble()` in reverse - and re-enters the same step chain
onboarding takes, at `name` rather than the path menu (a reconfigure never
changes the onboarding path, the same rule a subentry's own reconfigure
follows for its kind). Every step's own form already pre-fills from that
state, so what is new here is the restore and the finish: an update, not a
second entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerplan.const import (
    CONF_ACTIVE,
    CONF_ELECTRICAL,
    CONF_NAME,
    CONF_PRESENCE,
    CONF_PRICES,
    CONF_TARIFF,
    DOMAIN,
)
from tests.flows.test_site_flow import (
    ELECTRICAL_NO,
    NOTIFICATIONS,
    _answer,
    _configure,
    _followups,
    _start,
    _tail,
    _through_meter,
    _through_prices,
    _through_tariff,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _full_site(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, *, active: bool
) -> str:
    """Build a full-path site through the real onboarding flow; return its entry id."""
    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    result = await _through_tariff(hass, result)
    result = await _followups(hass, result)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=not active)
    await hass.async_block_till_done()
    return str(hass.config_entries.async_entries(DOMAIN)[0].entry_id)


async def _reconfigure(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Open the site's own reconfigure flow."""
    return dict(
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry_id}
        )
    )


async def _to_review(
    hass: HomeAssistant,
    entry_id: str,
    ams_meter: str,
    *,
    electrical: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-walk every step on its pre-filled answers, changing only the fuse if asked."""
    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, confirm="ok")
    result = await _answer(hass, result, **(electrical or ELECTRICAL_NO))
    result = await _answer(hass, result, source="nordpool_action")
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, preset="no/tensio-ts")
    result = await _answer(hass, result, confirm="yes")
    result = await _answer(hass, result, target="auto", risk="free_ride")
    result = await _answer(hass, result, mode="none")
    result = await _answer(hass, result, modifiers=["vat"])
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, carriers=[])
    result = await _answer(hass, result, mode="auto")
    result = await _answer(hass, result, persons=["person.joel", "person.kari"])
    result = await _answer(hass, result, **NOTIFICATIONS)
    assert result["step_id"] == "review", result
    return result


async def test_reconfigure_skips_the_path_menu_and_pre_fills_the_name(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The path never changes on a reconfigure - straight to `name`, pre-filled."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)

    result = await _reconfigure(hass, entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "name"
    assert result["data_schema"]({})[CONF_NAME] == "Hjemme"


async def test_reconfigure_pre_fills_the_meter_device_electrical_and_the_price_source(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The meter device (HUB-22), its roles, the fuse and the contract come back."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)

    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    assert result["step_id"] == "meter"
    assert result["data_schema"]({})["device"] == ams_meter, "HUB-22: the device is restored"
    result = await _answer(hass, result, device=ams_meter)

    assert result["step_id"] == "meter_confirm"
    # The power sensor used not to be pre-filled on a reconfigure. It is the
    # site's own stored role, shown and pre-filled.
    assert "Strømmåler Effekt" in result["description_placeholders"]["found"]
    result = await _answer(hass, result, confirm="change")
    assert result["step_id"] == "meter_roles"
    roles = result["data_schema"]({})
    assert roles["grid_power"] == "sensor.dataskap_strommaler_power"
    assert roles["import_register"] == "sensor.dataskap_strommaler_energy"
    result = await _answer(hass, result, **roles)

    assert result["step_id"] == "electrical"
    assert result["data_schema"]({})["main_fuse_a"] == "63"
    result = await _answer(hass, result, **ELECTRICAL_NO)

    assert result["step_id"] == "prices"
    assert result["data_schema"]({})["source"] == "nordpool_action"
    result = await _answer(hass, result, source="nordpool_action")
    # Shown on a reconfigure, to correct: the area by its region name (CTL-9).
    assert result["step_id"] == "prices_nordpool"
    assert result["data_schema"]({})["area"] == "NO3"
    assert "currency" not in result["data_schema"]({}), "HUB-2: the currency is never asked"


async def test_reconfigure_pre_fills_the_tariff_target_and_every_add_on(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The strictness the household chose and each add-on's options come back (HUB-9)."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)

    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, confirm="ok")
    result = await _answer(hass, result, **ELECTRICAL_NO)
    result = await _answer(hass, result, source="nordpool_action")
    result = await _answer(hass, result, **result["data_schema"]({}))

    assert result["step_id"] == "tariff"
    assert result["data_schema"]({})["preset"] == "no/tensio-ts"
    result = await _answer(hass, result, preset="no/tensio-ts")
    assert result["step_id"] == "tariff_preset"
    result = await _answer(hass, result)

    assert result["step_id"] == "tariff_target"
    assert result["data_schema"]({})["risk"] == "free_ride", "the site's own, not the new default"
    result = await _answer(hass, result, target="auto", risk="free_ride")

    assert result["step_id"] == "export"
    result = await _answer(hass, result, mode="none")
    assert result["step_id"] == "modifiers"
    assert result["data_schema"]({})["modifiers"] == ["vat"]
    result = await _answer(hass, result, modifiers=["vat"])
    assert result["step_id"] == "modifier_vat"
    assert result["data_schema"]({})["rate"] == 25, "the add-on's stored option, pre-filled"
    result = await _answer(hass, result, rate=25)
    assert result["step_id"] == "carriers"
    assert result["data_schema"]({})["carriers"] == []
    result = await _answer(hass, result, carriers=[])
    assert result["step_id"] == "presence"


async def test_reconfigure_keeps_an_active_site_active_and_shows_no_toggle(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """HUB-5: no trial-mode toggle on a reconfigure, and the entry's own `active` is kept."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=True)
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    assert entry.data[CONF_ACTIVE] is True

    result = await _to_review(hass, entry_id, ams_meter)
    assert result["data_schema"]({}) == {}, "no toggle on a reconfigure"
    result = await _answer(hass, result)
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    assert entry.data[CONF_ACTIVE] is True


async def test_reconfiguring_updates_the_same_entry_and_a_change_persists(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """A new fuse rating saves onto the same entry - no second site, no new id."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)
    before = hass.config_entries.async_get_entry(entry_id)
    assert before is not None
    unique_id = before.unique_id

    result = await _to_review(
        hass, entry_id, ams_meter, electrical={**ELECTRICAL_NO, "main_fuse_a": "80"}
    )
    result = await _answer(hass, result)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1, "a reconfigure updates the site, it does not add one"
    entry = entries[0]
    assert entry.entry_id == entry_id
    assert entry.unique_id == unique_id
    assert entry.data[CONF_ELECTRICAL]["main_fuse_a"] == 80.0
    assert entry.data[CONF_ACTIVE] is False, "observe stays observe"
    # The hard-limit step is gone; nothing writes `hard_limits` any more (S2).
    assert "hard_limits" not in entry.data
    assert entry.data[CONF_PRICES]["sources"][0]["key"] == "nordpool_action"
    assert entry.data[CONF_TARIFF]["preset_file"] == "no/tensio-ts"
    assert entry.data[CONF_PRESENCE]["persons"] == ["person.joel", "person.kari"]
