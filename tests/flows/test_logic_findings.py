"""D8 §9 21: the review's logic findings, through the real flow.

The core half of these checks - `Questionnaire.validate`'s cross-field bounds
and `chain_from`'s ordering - has its own tests
(`tests/core/loads/test_15b_questionnaire_cross_checks.py`,
`tests/core/pricing/test_spot_replacing_order.py`); this file drives the same
findings through `hass.config_entries` so the household actually sees the
refusal on the field, not a stack trace: (a) a required registry field with no
default is `vol.Required` and an empty submit is refused; (b) no site step
writes `hard_limits`, and an entry that still carries one from before this WP
computes the same `HardLimits` as one that does not; (e) a target above the
fuse, a comfort outside its own limits and a falling COP curve are all refused
on their own field. (c) is D4 §9 16's own test
(`tests/providers/profiles/test_16_generic_profile_match.py`); (d) is
§10-1, which stays U.3's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerplan.const import DOMAIN, SUBENTRY_LOAD
from tests.flows.test_load_flow import _answer as _load_answer
from tests.flows.test_site_flow import (
    ELECTRICAL_NO,
    _answer,
    _configure,
    _start,
    _through_meter,
    _through_tariff,
)
from tests.runtime.conftest import FakeMeter, site_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.powerplan.runtime import Runtime
    from tests.e2e.fake_house import FakeHouse


# --------------------------------------------------------------------------- #
# (a) a required field with no default is refused empty, never stored as 0
# --------------------------------------------------------------------------- #


async def _to_modifiers(hass: HomeAssistant, ams_meter: str, source: str) -> dict[str, Any]:
    """Walk a full site to the supplier's additions: the grid company first (D13 §6)."""
    _configure(hass)
    result = await _through_meter(hass, await _start(hass, "full"), ams_meter)
    result = await _through_tariff(hass, result)
    assert result["step_id"] == "prices"
    result = await _answer(hass, result, source=source)
    if result["step_id"] == "prices_nordpool":
        result = await _answer(hass, result, **result["data_schema"]({}))
    return result


@pytest.mark.inv("INV-49")
async def test_a_norgespris_style_price_with_no_default_refuses_an_empty_submit(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str
) -> None:
    """§10-2: `fixed_price.price` is required with no default; `render()` used to make it optional.

    `vol.Required` with no default is what a household's browser reads as "the
    submit button stays disabled until this is filled in" - there is no empty
    submission to refuse gracefully, because the frontend never sends one. This
    harness sends the dict a browser could not, and gets the same refusal
    voluptuous itself raises: `price` stays unfillable at 0.
    """
    # Norgespris is an answer to the contract question: the agreement itself, so
    # its price is asked whatever is ticked, and never offered as an addition (D13 §6 2).
    result = await _to_modifiers(hass, ams_meter, "norgespris")
    assert result["step_id"] == "modifiers"
    assert "fixed_price" not in result["data_schema"]({})["modifiers"]
    result = await _answer(hass, result, modifiers=[])
    assert result["step_id"] == "modifier_fixed_price"

    with pytest.raises(vol.Invalid):
        await _answer(hass, result)

    # A real answer clears it.
    result = await _answer(hass, result, price=50)
    assert result["step_id"] != "modifier_fixed_price"


@pytest.mark.inv("INV-49")
async def test_the_sites_own_fixed_price_source_refuses_an_empty_submit(
    hass: HomeAssistant,
) -> None:
    """The same rule for the site's own "one fixed price" source, not only an add-on."""
    _configure(hass)
    result = await _start(hass, "price_only")
    assert result["step_id"] == "electrical"
    result = await _answer(hass, result, **ELECTRICAL_NO)

    assert result["step_id"] == "prices"
    result = await _answer(hass, result, source="fixed")
    assert result["step_id"] == "prices_fixed"

    with pytest.raises(vol.Invalid):
        await _answer(hass, result)


@pytest.mark.inv("INV-49")
async def test_a_required_row_list_left_empty_refuses_with_the_field_named(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str
) -> None:
    """`cumulative_tier.tiers` is a required list; unlike a bare number, a browser CAN submit `[]`.

    That reaches `config_flow`'s own `missing_required` check (not voluptuous's
    presence check, which an empty list already satisfies), so it is refused
    with the field named rather than stored as a tier table with nothing in it.
    """
    result = await _to_modifiers(hass, ams_meter, "nordpool_action")
    assert result["step_id"] == "modifiers"
    result = await _answer(hass, result, modifiers=["cumulative_tier"])
    assert result["step_id"] == "modifier_cumulative_tier"

    result = await _answer(hass, result, tiers=[])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "modifier_cumulative_tier"
    assert result["errors"] == {"tiers": "required"}

    result = await _answer(hass, result, tiers=[{"price": 100}])
    assert result["step_id"] != "modifier_cumulative_tier"


# --------------------------------------------------------------------------- #
# (b) the hard-limit step is gone; a legacy entry's stored answer is ignored
# --------------------------------------------------------------------------- #


async def test_an_entry_that_still_carries_hard_limits_computes_the_same_limits(
    hass: HomeAssistant,
) -> None:
    """`HardLimits` comes from D3's fuse and the tariff, never `entry.data["hard_limits"]`."""
    with_legacy = site_data(hass)
    assert with_legacy["hard_limits"] == {"contracted_kw": 25.0}
    without_legacy = {k: v for k, v in site_data(hass).items() if k != "hard_limits"}

    FakeMeter(hass)
    await hass.async_block_till_done()

    # Two sites share one meter and one unique id, so they run one at a time:
    # set up, tick, read the snapshot's `hard_limit_w`, unload, repeat.
    hard_limit_ws: list[float | None] = []
    for title, data in (("A", with_legacy), ("B", without_legacy)):
        entry = MockConfigEntry(
            domain=DOMAIN, title=title, entry_id="01HARDLIMITSCHECK00", data=data
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        runtime: Runtime = entry.runtime_data
        await runtime.run_tick("test")
        snapshot = runtime.coordinator.data
        assert snapshot is not None
        hard_limit_ws.append(snapshot.tariff.hard_limit_w)
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    # The one number `HardLimits` contributes to the snapshot: the contracted
    # limit the tariff names, unaffected by the stale `hard_limits` key.
    assert hard_limit_ws[0] == hard_limit_ws[1]


# --------------------------------------------------------------------------- #
# (e) cross-field errors reach the field
# --------------------------------------------------------------------------- #


@pytest.mark.inv("INV-49")
async def test_a_tariff_target_above_the_fuse_is_refused_on_its_field(
    hass: HomeAssistant, ams_meter: str, nordpool_entry: str
) -> None:
    """A step whose lower bound is at or above what a small main fuse delivers (review CTL-16)."""
    _configure(hass)
    result = await _start(hass, "full")
    # A 10 A fuse on 3-phase 230 V IT delivers ≈ 3.98 kW - well under `no/tensio`'s
    # "5–10 kW" step, whose lower bound (5 kW) is what this checks against.
    small_fuse = {**ELECTRICAL_NO, "main_fuse_a": "10"}
    result = await _answer(hass, result, device=ams_meter)
    result = await _answer(hass, result, confirm="ok")
    result = await _answer(hass, result, **small_fuse)
    assert result["step_id"] == "postcode"
    result = await _answer(hass, result)

    assert result["step_id"] == "tariff"
    result = await _answer(hass, result, preset="operator:Tensio TS")
    result = await _answer(hass, result)

    assert result["step_id"] == "tariff_target"
    result = await _answer(hass, result, target="step_2", risk="free_ride")

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tariff_target"
    assert result["errors"] == {"target": "target_above_fuse"}


@pytest.mark.inv("INV-49")
async def test_a_comfort_outside_its_own_limits_reaches_the_load_flows_field(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """The water heater's ready temperature above its own ceiling (review CTL-5, CTL-16)."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    device_id = charger.loads["tank"].device_id
    result = await _load_answer(hass, result, type="water_heater")
    assert device_id is not None
    result = await _load_answer(hass, result, device=device_id)
    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    result = await _load_answer(hass, result, **suggested)

    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    # `max_c` stays at its default (80 °C); asking for 85 °C exceeds it.
    result = await _load_answer(
        hass, result, **{**defaults, "advanced": {**defaults["advanced"], "ready_temp_c": 85.0}}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "questions"
    assert result["errors"] == {"ready_temp_c": "outside_bounds"}


@pytest.mark.inv("INV-49")
async def test_a_cop_curve_that_falls_reaches_the_load_flows_field(
    hass: HomeAssistant, site: MockConfigEntry, charger: FakeHouse
) -> None:
    """A heat pump's COP has to rise as it gets warmer outside (review CTL-13, CTL-16)."""
    result = dict(
        await hass.config_entries.subentries.async_init(
            (site.entry_id, SUBENTRY_LOAD), context={"source": SOURCE_USER}
        )
    )
    device_id = charger.loads["heat_pump"].device_id
    result = await _load_answer(hass, result, type="heat_pump")
    assert device_id is not None
    result = await _load_answer(hass, result, device=device_id)
    assert result["step_id"] == "match", result
    suggested = result["data_schema"]({})
    result = await _load_answer(hass, result, **suggested)

    assert result["step_id"] == "questions", result
    defaults = result["data_schema"]({})
    falling = [{"outdoor_c": -10.0, "cop": 3.8}, {"outdoor_c": 7.0, "cop": 2.1}]
    result = await _load_answer(
        hass, result, **{**defaults, "advanced": {**defaults["advanced"], "cop_curve": falling}}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "questions"
    assert result["errors"] == {"cop_curve": "curve_not_rising"}
