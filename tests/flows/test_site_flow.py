"""The site config flow, end to end (D8 §9 item 1, D8 §5.1).

Everything here is driven through `hass.config_entries.flow`, never by injecting
entry data (D9 §5.10): the steps, their order, their skips and the entry they
produce are all under test at the boundary the household actually uses.

The three onboarding paths of HLD §4 are three tests. The invariants marked are
D8's own - INV-49 (validation lives in the schemas), INV-65 (advanced is never
required), INV-66 (what the flow derives is materialised) and INV-67 (no flow
ends on a bare "Success").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, section

from custom_components.powerplan.const import (
    CONF_ACTIVE,
    CONF_ELECTRICAL,
    CONF_METER,
    CONF_PATH,
    CONF_PRICES,
    CONF_TARIFF,
    CONF_TIMEZONE,
    CONF_TIMEZONE_SOURCE,
    DOMAIN,
    SECTION_ADVANCED,
    OnboardingPath,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SITE_TZ = "Europe/Oslo"
UNSET_TZ = "Mars/Olympus Mons"
CURRENCY = "NOK"


def _configure(hass: HomeAssistant, time_zone: str = SITE_TZ) -> None:
    """Set the two things the flow derives from the environment."""
    hass.config.time_zone = time_zone
    hass.config.currency = CURRENCY
    hass.config.country = "NO"


async def _start(hass: HomeAssistant, path: str, **context: Any) -> dict[str, Any]:
    """Open the flow, choose an onboarding path and answer the name step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER, **context}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"full", "price_only", "fuse_only"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": path}
    )
    assert result["step_id"] == "name"
    return dict(
        await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Hjemme"})
    )


async def _answer(hass: HomeAssistant, result: dict[str, Any], **user_input: Any) -> dict[str, Any]:
    """Submit one step and return the next."""
    return dict(await hass.config_entries.flow.async_configure(result["flow_id"], dict(user_input)))


#: The fuse question: the country and the phases come from Home Assistant (HUB-2).
ELECTRICAL_NO = {
    "system": "it_230",
    "main_fuse_a": "63",
}

NOTIFICATIONS = {
    "peak_warning": "persistent",
    "comfort_violation": "persistent",
    "device_unhealthy": "persistent",
    # A half-hour select now (CTL-7): no seconds.
    "quiet_start": "22:00",
    "quiet_end": "07:00",
}


async def _through_meter(
    hass: HomeAssistant, result: dict[str, Any], device_id: str
) -> dict[str, Any]:
    """Answer the meter, confirm what was found, then the fuse (questions 3 and 4)."""
    assert result["step_id"] == "meter"
    result = await _answer(hass, result, device=device_id)

    # D8 §9 21 (d): every found role shown with its value, the export register
    # among them - read through the meter provider, never an entity id.
    assert result["step_id"] == "meter_confirm", result
    found = result["description_placeholders"]["found"]
    assert "Strømmåler Effekt" in found
    assert "sensor." not in found
    assert result["data_schema"]({})["confirm"] == "ok"
    result = await _answer(hass, result, confirm="ok")

    assert result["step_id"] == "electrical"
    return await _answer(hass, result, **ELECTRICAL_NO)


async def _skip_meter(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Pick no meter device and bind no sensor: the role form, then the fuse."""
    assert result["step_id"] == "meter"
    result = await _answer(hass, result)
    assert result["step_id"] == "meter_roles"
    result = await _answer(hass, result)
    assert result["step_id"] == "electrical"
    return result


async def _through_prices(
    hass: HomeAssistant, result: dict[str, Any], nordpool_entry: str
) -> dict[str, Any]:
    """Answer the contract; the house's one Nord Pool entry names its area, so nothing more."""
    del nordpool_entry
    assert result["step_id"] == "prices"
    assert result["data_schema"]({})["source"] == "nordpool_action"
    return await _answer(hass, result, source="nordpool_action")


async def _through_tariff(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Answer the grid company, confirm the table, then target and strictness."""
    assert result["step_id"] == "tariff"
    assert "country" not in result["data_schema"]({}), "asked once, and HA has it (HUB-2)"
    result = await _answer(hass, result, preset="no/tensio-ts")

    assert result["step_id"] == "tariff_preset"
    # HUB-12: D2's `TariffSummary` rendered as a translated table, nothing stored.
    table = result["description_placeholders"]["table"]
    assert "3 highest hours on 3 different days" in table
    # Tensio TS's own table from 2026-07-01, fifteen steps to "over 500 kW".
    assert "| Above 500 kW | 21,473 kr |" in table
    assert result["description_placeholders"]["name"] == "Tensio TS – privatkunde"
    result = await _answer(hass, result, confirm="yes")

    assert result["step_id"] == "tariff_target"
    # D2 §6: strict is the default for a new site, on every preset.
    assert result["data_schema"]({})["risk"] == "flat"
    assert result["description_placeholders"]["hours"] == "3"
    return await _answer(hass, result, target="auto", risk="free_ride")


async def _followups(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Answer export, the add-ons and the heat sources - the follow-ups after the grid company."""
    assert result["step_id"] == "export"
    result = await _answer(hass, result, mode="none")

    assert result["step_id"] == "modifiers"
    # Norway's pre-tick: only a modifier whose required fields all have defaults
    # may be pre-ticked, so VAT is on and the grid charge arrives with the preset.
    assert result["data_schema"]({})["modifiers"] == ["vat"]
    result = await _answer(hass, result, modifiers=["vat"])

    # HUB-7: every add-on its own step, titled by its own strings - never by its key.
    assert result["step_id"] == "modifier_vat"
    assert not result["description_placeholders"]
    # A rate is a % box now, not a fraction box (CTL-2); stored as the fraction.
    result = await _answer(hass, result, rate=25)

    assert result["step_id"] == "carriers"
    return await _answer(hass, result, carriers=[])


async def _tail(hass: HomeAssistant, result: dict[str, Any]) -> dict[str, Any]:
    """Answer presence and notifications; stop on the review.

    The hard-limit step is gone (its answer was read by nothing, D8 §5.15 S2,
    review NEW-1): the grense is D3's fuse and per-phase limit.
    """
    assert result["step_id"] == "presence"
    result = await _answer(hass, result, mode="auto")

    assert result["step_id"] == "presence_persons"
    result = await _answer(hass, result, persons=["person.joel", "person.kari"])

    assert result["step_id"] == "notifications"
    result = await _answer(hass, result, **NOTIFICATIONS)

    assert result["step_id"] == "review"
    return result


@pytest.mark.inv("INV-66")
@pytest.mark.inv("INV-67")
async def test_the_full_path_creates_a_site_in_observe(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """Meter, prices and tariff, end to end, with every derivation materialised."""
    _configure(hass)

    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    result = await _through_tariff(hass, result)
    result = await _followups(hass, result)
    result = await _tail(hass, result)

    result = await _answer(hass, result, start_in_observe=True)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hjemme"
    data = result["data"]

    assert data[CONF_PATH] == OnboardingPath.FULL
    assert data[CONF_ACTIVE] is False
    assert data[CONF_TIMEZONE] == SITE_TZ

    # INV-66: D3's numbers are computed once, at setup, and stored.
    derived = data[CONF_ELECTRICAL]["derived"]
    assert derived["fuse_w"] == pytest.approx(63.0 * 3**0.5 * 230.0)
    assert derived["plausible_w"][1] == pytest.approx(1.2 * derived["fuse_w"])
    assert data[CONF_ELECTRICAL]["per_phase_limit_a"] == 63.0

    assert data[CONF_METER]["roles"]["import_register"] == "sensor.dataskap_strommaler_energy"

    prices = data[CONF_PRICES]
    assert prices["sources"][0]["key"] == "nordpool_action"
    assert prices["sources"][0]["options"]["area"] == "NO3"
    assert (
        prices["modifiers"][0]["key"],
        {"rate": prices["modifiers"][0]["options"]["rate"]},
    ) == ("vat", {"rate": "0.25"})
    # D2 §6: the preset's own energy components are handed to D1 as a modifier.
    grid = next(mod for mod in prices["modifiers"] if mod["key"] == "tou_schedule")
    assert grid["source"] == "no.tensio-ts.household"

    tariff = data[CONF_TARIFF]
    assert tariff["preset_id"] == "no.tensio-ts.household"
    assert tariff["preset_file"] == "no/tensio-ts"
    assert tariff["version_ids"] == [
        "no.tensio-ts.household@2025-07-01",
        "no.tensio-ts.household@2026-01-01",
        "no.tensio-ts.household@2026-07-01",
    ]
    # The entry keeps its own copy of the tariff (D2 §6, INV-66): a release that
    # edits or retires the file never moves this site's ceiling.
    assert tariff["spec"]["id"] == "no.tensio-ts.household"
    assert [version["valid_from"] for version in tariff["spec"]["versions"]] == [
        "2025-07-01",
        "2026-01-01",
        "2026-07-01",
    ]
    assert tariff["risk"] == 0.5
    assert tariff["risk_source"] == "chosen", "the household picked it over the strict default"


@pytest.mark.inv("INV-50")
async def test_the_unique_id_is_the_meters_own_register(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The entry is identified by the import register's platform unique id."""
    _configure(hass)

    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    result = await _through_tariff(hass, result)
    result = await _followups(hass, result)
    result = await _tail(hass, result)
    await _answer(hass, result, start_in_observe=True)
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "meter:ams_han:dataskap_strommaler_energy"


async def test_the_price_only_path_skips_the_meter_and_the_tariff(
    hass: HomeAssistant, nordpool_entry: str, persons: list[str]
) -> None:
    """Price only: no meter step, no tariff step, and the capacity axis off."""
    _configure(hass)

    result = await _start(hass, "price_only")
    assert result["step_id"] == "electrical"
    result = await _answer(hass, result, **ELECTRICAL_NO)

    result = await _through_prices(hass, result, nordpool_entry)
    result = await _followups(hass, result)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)
    await hass.async_block_till_done()

    data = result["data"]
    assert data[CONF_PATH] == OnboardingPath.PRICE_ONLY
    assert data[CONF_METER] is None
    assert data[CONF_TARIFF]["preset_id"] == "no_peak"
    assert data[CONF_PRICES]["sources"][0]["key"] == "nordpool_action"


async def test_the_fuse_only_path_has_no_prices_and_no_tariff(
    hass: HomeAssistant, ams_meter: str, persons: list[str]
) -> None:
    """Fuse only: dynamic load balancing, nothing priced."""
    _configure(hass)

    result = await _start(hass, "fuse_only")
    result = await _through_meter(hass, result, ams_meter)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)
    await hass.async_block_till_done()

    data = result["data"]
    assert data[CONF_PATH] == OnboardingPath.FUSE_ONLY
    assert data[CONF_PRICES] is None
    assert data[CONF_TARIFF] is None
    assert data[CONF_METER]["roles"]["grid_power"] == "sensor.dataskap_strommaler_power"


@pytest.mark.inv("INV-66")
async def test_the_timezone_comes_from_home_assistant(
    hass: HomeAssistant, ams_meter: str, persons: list[str]
) -> None:
    """With a zone configured there is no timezone step; the zone is stored."""
    _configure(hass)

    result = await _start(hass, "fuse_only")
    assert result["step_id"] == "meter"

    result = await _through_meter(hass, result, ams_meter)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)

    assert result["data"][CONF_TIMEZONE] == SITE_TZ
    assert result["data"][CONF_TIMEZONE_SOURCE] == "hass"


@pytest.mark.inv("INV-66")
async def test_the_timezone_step_appears_when_home_assistant_has_none(
    hass: HomeAssistant, ams_meter: str, persons: list[str]
) -> None:
    """An unusable `hass.config.time_zone` is the only reason to ask."""
    _configure(hass, UNSET_TZ)

    result = await _start(hass, "fuse_only")
    assert result["step_id"] == "timezone"
    result = await _answer(hass, result, timezone=SITE_TZ)

    result = await _through_meter(hass, result, ams_meter)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)

    assert result["data"][CONF_TIMEZONE] == SITE_TZ
    assert result["data"][CONF_TIMEZONE_SOURCE] == "user"


@pytest.mark.inv("INV-49")
async def test_a_guard_band_in_watts_is_refused_inline(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """D2 §6: `eps_kwh > 2.0` is watts wearing a kWh label."""
    _configure(hass)

    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)

    assert result["step_id"] == "tariff"
    result = await _answer(hass, result, preset="no/tensio-ts")
    result = await _answer(hass, result)

    assert result["step_id"] == "tariff_target"
    result = await _answer(
        hass, result, target="auto", risk="free_ride", advanced={"eps_kwh": 300.0}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tariff_target"
    assert result["errors"] == {"eps_kwh": "eps_looks_like_watts"}


@pytest.mark.inv("INV-49")
async def test_an_implausible_fuse_is_refused_inline(hass: HomeAssistant) -> None:
    """D3 §6: a fuse below 6 A or above 400 A is not a grid connection."""
    _configure(hass)

    result = await _skip_meter(hass, await _start(hass, "fuse_only"))
    result = await _answer(hass, result, **ELECTRICAL_NO, advanced={"per_phase_limit_a": 900.0})

    assert result["step_id"] == "electrical"
    assert result["errors"] == {"per_phase_limit_a": "fuse_out_of_range"}


async def test_a_step_reshown_keeps_what_was_answered(hass: HomeAssistant) -> None:
    """Back navigation: every step but the review is `last_step=False`."""
    _configure(hass)

    result = await _start(hass, "fuse_only")
    assert result["step_id"] == "meter"
    assert result["last_step"] is False

    result = await _skip_meter(hass, result)
    assert result["last_step"] is False

    reshown = dict(await hass.config_entries.flow.async_configure(result["flow_id"]))
    assert reshown["step_id"] == "electrical"
    assert reshown["last_step"] is False


@pytest.mark.inv("INV-65")
async def test_advanced_is_never_required(
    hass: HomeAssistant, ams_meter: str, persons: list[str]
) -> None:
    """Advanced is a collapsed section, pre-filled, and never required."""
    _configure(hass)

    result = await _start(hass, "fuse_only")
    result = await _through_meter_roles_only(hass, result, ams_meter)
    assert result["step_id"] == "electrical"
    rendered = result["data_schema"].schema
    advanced = next(value for key, value in rendered.items() if str(key) == SECTION_ADVANCED)
    assert isinstance(advanced, section)
    assert advanced.options["collapsed"] is True
    assert {str(key) for key in advanced.schema.schema} == {"phases", "per_phase_limit_a"}

    # Answering the step without opening the section at all still works.
    result = await _answer(hass, result, **ELECTRICAL_NO)
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ELECTRICAL]["per_phase_limit_a"] == 63.0


async def _through_meter_roles_only(
    hass: HomeAssistant, result: dict[str, Any], device_id: str
) -> dict[str, Any]:
    """Answer the device pick, choose to change, and accept every pre-filled role."""
    assert result["step_id"] == "meter"
    result = await _answer(hass, result, device=device_id)
    assert result["step_id"] == "meter_confirm"
    result = await _answer(hass, result, confirm="change")
    assert result["step_id"] == "meter_roles"
    return await _answer(hass, result, **result["data_schema"]({}))


@pytest.mark.inv("INV-67")
async def test_the_review_explains_what_was_derived(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The review names the tariff, the timezone and the observe start."""
    _configure(hass)

    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    result = await _through_tariff(hass, result)
    result = await _followups(hass, result)
    result = await _tail(hass, result)

    placeholders = result["description_placeholders"]
    assert placeholders["tariff"] == "Tensio TS – privatkunde · Automatic"
    assert placeholders["timezone"] == SITE_TZ
    assert "Home Assistant" in placeholders["timezone_source"]
    assert "63" in placeholders["connection"]
    # HUB-14: the household's own names for its sensors and persons, never their ids.
    assert "sensor." not in placeholders["meter"]
    assert placeholders["meter"].startswith("Strømmåler Effekt and ")
    assert placeholders["meter"].endswith(" and 2 more")
    assert "NO3" in placeholders["prices"]
    assert placeholders["presence"] == "Joel and Kari"
    # D10 §6: nothing to ask, so the review says what was found - a meter is
    # bound in this flow, so the baseline half is offered, not "not configured".
    assert "Normal usage is learned from the meter" in placeholders["forecasts"]
    assert result["last_step"] is True


@pytest.mark.inv("INV-49")
async def test_three_phases_on_a_single_phase_supply_is_refused_inline(
    hass: HomeAssistant,
) -> None:
    """D3 §5.1: a phase count the supply system has no conversion for.

    Not a split-phase service: that one delivers two legs whatever `phases` says,
    so `ElectricalProfile.service_phases()` answers for it and the combination is
    meaningful. A 230 V single-phase supply with three phases is the one the
    domain type itself refuses.
    """
    _configure(hass)

    hass.config.country = "GB"
    result = await _skip_meter(hass, await _start(hass, "fuse_only"))
    result = await _answer(
        hass, result, system="single_230", main_fuse_a="63", advanced={"phases": "3"}
    )

    assert result["step_id"] == "electrical"
    assert result["errors"] == {"phases": "phases_not_available"}


@pytest.mark.inv("INV-49")
async def test_a_power_role_bound_to_degrees_is_refused_inline(
    hass: HomeAssistant, ams_meter: str
) -> None:
    """D3 §6: a power entity whose unit is neither W nor kW is refused."""
    _configure(hass)

    result = await _start(hass, "fuse_only")
    assert result["step_id"] == "meter"
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, confirm="change")

    assert result["step_id"] == "meter_roles"
    roles = {**result["data_schema"]({}), "grid_power": "sensor.dataskap_strommaler_temperature"}
    result = await _answer(hass, result, **roles)

    assert result["step_id"] == "meter_roles"
    assert result["errors"] == {"grid_power": "power_unit_not_watts"}


@pytest.mark.inv("INV-49")
async def test_a_guard_band_of_zero_is_refused_inline(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """D2 §6: the guard band is what covers meter cadence; zero covers nothing."""
    _configure(hass)

    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)

    assert result["step_id"] == "tariff"
    result = await _answer(hass, result, preset="no/tensio-ts")
    result = await _answer(hass, result)

    assert result["step_id"] == "tariff_target"
    result = await _answer(hass, result, target="auto", risk="free_ride", advanced={"eps_kwh": 0.0})

    assert result["step_id"] == "tariff_target"
    assert result["errors"] == {"eps_kwh": "eps_not_positive"}


async def test_the_grid_companys_own_charges_are_not_offered_again_as_add_ons(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str, persons: list[str]
) -> None:
    """The preset prices the day/night charge; the add-on step names it and does not offer it.

    Asked after the grid company (HUB-3), so the step knows what the preset has
    (D-0430). A tick for it cannot replace the preset's numbers with empty ones.
    """
    _configure(hass)
    result = await _start(hass, "full")
    result = await _through_meter(hass, result, ams_meter)
    result = await _through_prices(hass, result, nordpool_entry)
    result = await _through_tariff(hass, result)
    result = await _answer(hass, result, mode="none")

    assert result["step_id"] == "modifiers"
    offered = [
        option
        for value in result["data_schema"].schema.values()
        if hasattr(value, "config")
        for option in value.config["options"]
    ]
    assert "tou_schedule" not in offered, "the preset already prices it"
    # Tensio's figures include forbruksavgift and Enova, so the levy is not
    # offered again (D-0523); VAT still taxes the spot price, so it is.
    assert "levy" not in offered
    assert "vat" in offered
    assert result["description_placeholders"]["included"] == (
        "Grid energy charge (day/night) and Taxes and levies"
    )
    result = await _answer(hass, result, modifiers=["vat"])
    result = await _answer(hass, result, rate=25)
    result = await _answer(hass, result, carriers=[])
    result = await _tail(hass, result)
    result = await _answer(hass, result, start_in_observe=True)

    grid = [mod for mod in result["data"][CONF_PRICES]["modifiers"] if mod["key"] == "tou_schedule"]
    assert len(grid) == 1
    assert grid[0]["source"] == "no.tensio-ts.household"
    assert grid[0]["options"]["periods"], "the preset's day/night numbers, not an empty table"
