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


async def test_reconfigure_pre_fills_electrical_meter_roles_and_the_price_source(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """Fuse, phases and meter roles - the answer the household gave - come back."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)

    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    assert result["step_id"] == "electrical"
    assert result["data_schema"]({})["main_fuse_a"] == "63"

    result = await _answer(hass, result, **ELECTRICAL_NO)
    assert result["step_id"] == "meter"
    result = await _answer(hass, result, device=ams_meter)

    assert result["step_id"] == "meter_roles"
    roles = result["data_schema"]({})
    assert roles["import_register"] == "sensor.dataskap_strommaler_energy"

    result = await _answer(hass, result, **roles)
    assert result["step_id"] == "prices"
    assert result["data_schema"]({})["source"] == "nordpool_action"

    result = await _answer(hass, result, source="nordpool_action")
    assert result["step_id"] == "prices_nordpool"
    assert result["data_schema"]({})["area"] == "NO3"


async def test_reconfigure_pre_fills_the_tariff_target(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The risk posture the household already confirmed comes back.

    The hard-limit step is gone (D8 §5.15 S2, review NEW-1): its answer was
    read by nothing, and the grense is D3's own fuse and per-phase limit - so
    the tariff target step now leads straight to presence.
    """
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)

    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    result = await _answer(hass, result, **ELECTRICAL_NO)
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, source="nordpool_action")
    result = await _answer(hass, result, **result["data_schema"]({}))

    assert result["step_id"] == "modifiers"
    assert result["data_schema"]({})["modifiers"] == ["vat"]
    result = await _answer(hass, result, modifiers=["vat"])
    assert result["step_id"] == "modifier_vat"
    result = await _answer(hass, result, rate=25)
    assert result["step_id"] == "export"
    result = await _answer(hass, result, mode="none")
    assert result["step_id"] == "carriers"
    assert result["data_schema"]({})["carriers"] == []
    result = await _answer(hass, result, carriers=[])

    assert result["step_id"] == "tariff"
    assert result["data_schema"]({})["preset"] == "no/tensio"
    result = await _answer(hass, result, country="NO", preset="no/tensio")
    assert result["step_id"] == "tariff_preset"
    result = await _answer(hass, result)

    assert result["step_id"] == "tariff_target"
    assert result["data_schema"]({})["risk"] == "free_ride"
    result = await _answer(hass, result, target="auto", risk="free_ride")

    assert result["step_id"] == "presence"


async def test_reconfigure_keeps_an_active_site_active_by_default(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The review checkbox reflects the site's own state - active stays active."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=True)
    entry = hass.config_entries.async_get_entry(entry_id)
    assert entry is not None
    assert entry.data[CONF_ACTIVE] is True

    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    result = await _answer(hass, result, **ELECTRICAL_NO)
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, source="nordpool_action")
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, modifiers=["vat"])
    result = await _answer(hass, result, rate=25)
    result = await _answer(hass, result, mode="none")
    result = await _answer(hass, result, carriers=[])
    result = await _answer(hass, result, country="NO", preset="no/tensio")
    result = await _answer(hass, result)
    result = await _answer(hass, result, target="auto", risk="free_ride")
    # The hard-limit step is gone (D8 §5.15 S2): tariff_target leads to presence.
    assert result["step_id"] == "presence"
    result = await _answer(hass, result, mode="auto")
    assert result["step_id"] == "presence_persons"
    result = await _answer(hass, result, persons=["person.joel", "person.kari"])
    assert result["step_id"] == "notifications"
    result = await _answer(hass, result, **NOTIFICATIONS)

    assert result["step_id"] == "review"
    assert result["data_schema"]({})["start_in_observe"] is False


async def test_reconfiguring_updates_the_same_entry_and_a_change_persists(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """A new fuse rating saves onto the same entry - no second site, no new id."""
    _configure(hass)
    entry_id = await _full_site(hass, ams_meter, nordpool_entry, active=False)
    before = hass.config_entries.async_get_entry(entry_id)
    assert before is not None
    unique_id = before.unique_id

    changed_electrical = {**ELECTRICAL_NO, "main_fuse_a": "80"}
    result = await _reconfigure(hass, entry_id)
    result = await _answer(hass, result, name="Hjemme")
    result = await _answer(hass, result, **changed_electrical)
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, source="nordpool_action")
    result = await _answer(hass, result, **result["data_schema"]({}))
    result = await _answer(hass, result, modifiers=["vat"])
    result = await _answer(hass, result, rate=25)
    result = await _answer(hass, result, mode="none")
    result = await _answer(hass, result, carriers=[])
    result = await _answer(hass, result, country="NO", preset="no/tensio")
    result = await _answer(hass, result)
    result = await _answer(hass, result, target="auto", risk="free_ride")
    # The hard-limit step is gone (D8 §5.15 S2): tariff_target leads to presence.
    result = await _answer(hass, result, mode="auto")
    result = await _answer(hass, result, persons=["person.joel", "person.kari"])
    result = await _answer(hass, result, **NOTIFICATIONS)
    assert result["step_id"] == "review"
    result = await _answer(hass, result, start_in_observe=True)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1, "a reconfigure updates the site, it does not add one"
    entry = entries[0]
    assert entry.entry_id == entry_id
    assert entry.unique_id == unique_id
    assert entry.data[CONF_ELECTRICAL]["main_fuse_a"] == 80.0
    # The hard-limit step is gone; nothing writes `hard_limits` any more (S2).
    assert entry.data[CONF_PRICES]["sources"][0]["key"] == "nordpool_action"
    assert entry.data[CONF_TARIFF]["preset_file"] == "no/tensio"
    assert entry.data[CONF_PRESENCE]["persons"] == ["person.joel", "person.kari"]
