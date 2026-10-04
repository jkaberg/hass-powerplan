"""D8 §9 40, 41; D11 §9 35 - the month's results on the surface, and resetting a month's books.

`sensor.<site>_savings` carries the step and metric with and without PowerPlan
and the price paid against the reference (D11 §5.10); `sensor.<site>_deviations`
counts the month's missed deadlines, comfort episodes and windows over (D7
§5.10); `powerplan.reset_accounting` restarts the books after a fault (D11 §5.11).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized

from custom_components.powerplan.const import DOMAIN
from custom_components.powerplan.core.engine import DeviationsState
from custom_components.powerplan.core.model import Money

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

    from custom_components.powerplan.runtime import Runtime

SAVINGS = "sensor.test_site_estimated_savings_this_month"
DEVIATIONS = "sensor.test_site_deviations_this_month"


async def test_40_the_savings_sensor_carries_the_results(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """The seven result attributes are published, `None` before the ledger has them."""
    state = hass.states.get(SAVINGS)
    assert state is not None
    for key in (
        "capacity_step",
        "capacity_step_without",
        "metric_kw",
        "metric_kw_without",
        "price_paid",
        "price_reference",
        "kwh_counted",
    ):
        assert key in state.attributes, key


async def test_48_the_savings_sensor_carries_the_day_profiles_unrecorded(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """`day_profile` and `previous_day_profile` are published and kept out of the recorder (D11 §5.12)."""
    state = hass.states.get(SAVINGS)
    assert state is not None
    assert "day_profile" in state.attributes
    assert "previous_day_profile" in state.attributes
    assert state.state_info is not None
    assert {"day_profile", "previous_day_profile"} <= state.state_info["unrecorded_attributes"]
    profile = state.attributes["day_profile"]
    if profile is not None:
        assert len(profile["kwh"]) == len(profile["cf_kwh"]) == 24
        assert set(profile) == {"month", "kwh", "cf_kwh", "days"}


async def test_40_the_deviations_sensor_sums_the_month(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """Missed deadlines + comfort episodes + windows over, the rows by load id, the month's start."""
    runtime: Runtime = site.runtime_data
    runtime.state = replace(
        runtime.state,
        runtime=replace(
            runtime.state.runtime,
            deviations=DeviationsState(
                month="2026-01",
                comfort_s={"floor": 1800.0},
                comfort_n={"floor": 2},
                deadline_met={"ev": 3},
                deadline_missed={"ev": 1},
                over_windows=1,
                windows=300,
            ),
        ),
    )
    entity = hass.data["entity_components"]["sensor"].get_entity(DEVIATIONS)
    assert entity is not None
    entity.async_write_ha_state()
    state = hass.states.get(DEVIATIONS)
    assert state is not None
    assert state.state == "4"
    assert state.attributes["comfort_min"] == {"floor": 30}
    assert state.attributes["deadlines_missed"] == {"ev": 1}
    assert state.attributes["over_windows"] == 1
    assert state.attributes["last_reset"].startswith("2026-01-01T00:00:00")


async def test_41_reset_accounting_needs_a_site_and_an_administrator(
    hass: HomeAssistant, site: MockConfigEntry, hass_read_only_user: MockUser
) -> None:
    """No site is a schema error; a user who is not an administrator is refused."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "reset_accounting", {}, blocking=True)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "reset_accounting",
            {"site": site.entry_id},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


async def test_35_reset_accounting_restarts_the_books(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """A ledger holding a fault's 3 161 NOK reads nothing after the reset, and the claim is gone."""
    runtime: Runtime = site.runtime_data
    assert runtime.adapter is not None
    ledger = runtime.adapter.accounting.state().ledger
    ledger.site.energy_cost = Money(Decimal("3161.56"), ledger.site.energy_cost.currency)
    history = runtime.build.tariff.history
    history.counterfactual_days = dict(history.days)

    await hass.services.async_call(
        DOMAIN, "reset_accounting", {"site": site.entry_id}, blocking=True
    )

    restarted = runtime.adapter.accounting.state().ledger
    assert restarted.site.energy_cost.amount == 0
    assert all(rec.cost.amount == 0 for rec in restarted.loads.values())
    assert history.counterfactual_days.keys() >= history.days.keys()
    assert runtime.state.accounting == runtime.adapter.section()


async def test_50_the_step_below_is_advice_and_the_levelled_step_an_attribute(
    hass: HomeAssistant, site: MockConfigEntry
) -> None:
    """D8 §9 50, D-0717: last month's levelled step under its actual one is advice, never a target."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    from custom_components.powerplan.entity import unique_id  # noqa: PLC0415

    runtime: Runtime = site.runtime_data
    snapshot = runtime.coordinator.data
    assert snapshot is not None
    results = {
        **snapshot.accounting.results,
        "levelled_level": "2–5 kW",
        "levelled_metric_kw": 4.2,
        "step_below": {"month": "2026-09", "step": "2–5 kW", "fee_delta": "164", "currency": "NOK"},
    }
    runtime.coordinator.async_set_updated_data(
        replace(snapshot, accounting=replace(snapshot.accounting, results=results))
    )
    await hass.async_block_till_done()

    savings = hass.states.get(SAVINGS)
    assert savings is not None
    assert savings.attributes["capacity_step_levelled"] == "2–5 kW"
    assert savings.attributes["metric_kw_levelled"] == pytest.approx(4.2)

    advice_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, unique_id(site.entry_id, "advice")
    )
    assert advice_id is not None
    advice = hass.states.get(advice_id)
    assert advice is not None
    items = advice.attributes["items"]
    (below,) = [item for item in items if item["key"] == "step_below"]
    assert below == {
        "key": "step_below",
        "severity": "info",
        "month": "2026-09",
        "step": "2–5 kW",
        "fee_delta": "164",
        "currency": "NOK",
    }
    assert "step_below" in advice.attributes["options"]

    runtime.coordinator.async_set_updated_data(
        replace(
            snapshot,
            accounting=replace(snapshot.accounting, results={**results, "step_below": None}),
        )
    )
    await hass.async_block_till_done()
    advice = hass.states.get(advice_id)
    assert advice is not None
    assert not [item for item in advice.attributes["items"] if item["key"] == "step_below"]
